# ER-модель — кейс 3 «Онлайн-чаевые»

PK — уникальный ключ записи. FK — ссылка на запись другой сущности. 1:N означает «одна запись связана со многими». Ниже проектная модель; её дополнительные поля не добавляются в исходные CSV на текущем этапе.

## 1. Сущности

| Сущность | Что хранит | Зачем |
|---|---|---|
| Merchant | merchant_id, name, status, description | справочник мерчантов |
| Employee | employee_id, merchant_id, name, payment_details | сотрудники ресторана и маски реквизитов выплат |
| QR | qr_id, merchant_id, employee_id, receipt_id, nonce, exp, signature | подписанная ссылка на конкретный чек |
| Payment | payment_id, qr_id, merchant_id, employee_id, amount, tip, status, timestamp, idempotency_key, client_session_id | попытка платежа и защита от повторного создания |
| Report | report_id, merchant_id, period, tips_total и расчётные показатели | отчёт по заведению за период |
| AuditEvent | event_id, payment_id, qr_id, action, from_status, to_status, amount_total, actor, at, error_code | история действий и ошибок |

## 2. Атрибуты

### 2.1. Merchant (ОБРАЗЕЦ — не трогать)

|Поле|Тип|PK/FK|Обяз.|Ограничение|
|-|-|-|-|-|
|merchant\_id|string|**PK**|да|`m\_NNN`|
|name|string|—|да|1–100|
|status|enum|—|да|ACTIVE/BLOCKED/PENDING|
|description|string|—|нет|0–200|

### 2.2. Employee

| Поле | Тип | PK/FK | Обяз. | Ограничение |
|---|---|---|---|---|
| employee_id | string | PK | да | уникальный e_NNN |
| merchant_id | string | FK → Merchant.merchant_id | да | существующий m_NNN |
| name | string | — | да | 1–100 символов |
| payment_details | string | — | да | только ****NNNN; полные реквизиты не хранятся |

### 2.3. QR

| Поле | Тип | PK/FK | Обяз. | Ограничение |
|---|---|---|---|---|
| qr_id | string | PK | да | уникальный qr_NNN |
| merchant_id | string | FK → Merchant.merchant_id | да | при выпуске ресторан ACTIVE |
| employee_id | string | FK → Employee.employee_id | да | сотрудник принадлежит merchant_id QR |
| receipt_id | string | — | да | r_NNNN, уникален в паре с merchant_id |
| nonce | string | — | да | 6 hex; уникален среди выпущенных QR, при коллизии генерируется заново |
| exp | datetime UTC | — | да | ISO 8601, при выпуске now + 24 часа |
| signature | string | — | да | 16 hex, усечённый HMAC-SHA256 |

Резерв QR на активную попытку и окончательное использование определяются через Payment. Одновременно допускается не более одного CREATED/PENDING и не более одного SUCCESS для одного qr_id. После DECLINED/CANCELED разрешена новая попытка до exp. Для переиздания ссылки того же чека нужна отдельная версия QR или аннулирование прежней записи; это расширение за пределами базовой CSV-модели, не дублирование receipt_id без правил.

### 2.4. Payment

| Поле | Тип | PK/FK | Обяз. | Ограничение |
|---|---|---|---|---|
| payment_id | string | PK | да | уникальный p_NNNN |
| qr_id | string | FK → QR.qr_id | да для новых платежей | отсутствует в историческом payments.csv; связь старых записей нельзя считать доказанной по номеру |
| amount | decimal(12,2) | — | да | 0 < amount ≤ 1 000 000 |
| tip | decimal(12,2) | — | да | 0 ≤ tip ≤ amount × 0.5 после округления до двух знаков |
| status | enum | — | да | CREATED/PENDING/SUCCESS/DECLINED/CANCELED |
| timestamp | datetime UTC | — | да | время создания, ISO 8601 |
| merchant_id | string | FK → Merchant.merchant_id | да | совпадает с мерчантом QR |
| employee_id | string | FK → Employee.employee_id | да | совпадает с сотрудником QR |
| idempotency_key | string | уникальный ключ вместе с client_session_id | да для новых платежей | непустой; численный лимит длины в исходном задании не задан; повтор с изменёнными параметрами запрещён |
| client_session_id | string | — | да для новых платежей | непрозрачный идентификатор клиентской сессии, не персональные данные |

amount_total=amount+tip вычисляется, не является отдельной колонкой исходного файла. Проверка чаевых выполняется по округлённому денежному пределу: например, половина суммы с нечётной копейкой округляется HALF_UP. Это уточняет математический предел из образца словаря.

В CLI токен подтверждения вычисляется через HMAC от payment_id, проверяется при confirm и не выводится в аудит. Клиентские сессии и их авторизация относятся к проектной модели HTTP-платформы и в локальном CLI отсутствуют. YAML-контракт заполнен.

### 2.5. Report

| Поле | Тип | PK/FK | Обяз. | Ограничение |
|---|---|---|---|---|
| report_id | string | PK | да при сохранении | уникальный rep_NNNN |
| merchant_id | string | FK → Merchant.merchant_id | да | существующий ресторан |
| period | string | — | да | YYYY-MM, корректный месяц |
| tips_total | decimal(12,2) | — | да | сумма чаевых SUCCESS за период, ≥ 0 |
| payments_count | integer | — | да | число всех попыток за период, ≥ 0 |
| avg_tip_pct | decimal | — | нет | среднее tip/amount × 100 по SUCCESS; null при отсутствии успехов |
| success_rate | decimal | — | нет | число SUCCESS / все попытки; от 0 до 1, null при отсутствии попыток |

Report — расчётное представление. Его можно не хранить отдельно; report_id нужен только для сохранённого снимка. Личный и дневной разрезы вычисляются из Payment с дополнительной фильтрацией/группировкой (09_reports.md).

### 2.6. AuditEvent

| Поле | Тип | PK/FK | Обяз. | Ограничение |
|---|---|---|---|---|
| event_id | string | PK | да | уникальный evt_NNNN |
| payment_id | string/null | FK → Payment.payment_id | для событий платежа | null, если запрос отклонён до создания платежа |
| action | enum | — | да | payment_created / payment_confirmed / payment_succeeded / payment_declined / payment_canceled / gateway_timeout / request_rejected |
| from_status | enum/null | — | нет | состояние Payment до события; null при создании и отказе до создания |
| to_status | enum/null | — | для событий платежа | состояние после события; null у отказа до создания |
| actor | string | — | да | system / client:<session_id> / officer:<employee_id> / merchant:<merchant_id> / admin:<id>; без ФИО и секретов |
| at | datetime UTC | — | да | момент события, ISO 8601 |
| qr_id | string/null | FK → QR.qr_id | если QR найден | null для неизвестного QR |
| amount_total | decimal(12,2)/null | — | для событий платежа | сумма ≥ 0; null до валидного расчёта |
| error_code | string/null | — | при ошибке | код из раздела 6; null при успехе |

Отклонённые запросы также регистрируются; они не обязаны иметь Payment. События добавляются без перезаписи истории.

## 3. Связи

| Связь | Тип | Обоснование |
|---|---|---|
| Merchant → Employee | 1:N | В ресторане много сотрудников; каждый сотрудник связан с одним рестораном |
| Merchant → QR | 1:N | Ресторан выпускает много QR |
| Employee → QR | 1:N | Официант выпускает QR для разных чеков |
| QR → Payment | 1:N | Возможны последовательные попытки после отказа или отмены; успешная не более одной |
| Merchant → Report | 1:N | Много периодов отчётности; каждый отчёт относится к одному ресторану |
| Payment → AuditEvent | 1:N | У платежа много событий; у события до создания платежа FK может отсутствовать |

Дополнительно Merchant → Payment и Employee → Payment — 1:N, QR → AuditEvent — 1:N. Связность дублирующих merchant_id/employee_id проверяется сервером.

## 4. Спецификация диаграммы draw.io

Для будущей диаграммы нужны шесть блоков из раздела 1; внутри — поля раздела 2 с PK/FK, между ними — связи раздела 3 с обозначениями 1 и N. Подписать ограничения «одна активная попытка» и «не более одного SUCCESS на QR». Для AuditEvent отметить необязательный payment_id.

Диаграмма создана: [er_model.drawio](output/diagrams/er_model.drawio), [PNG](output/diagrams/er_model.png). Изображение включено в приложение PDF.

## 5. Критерий приёмки

- [x] Описаны 6 сущностей, PK/FK, типы, ограничения и связи.
- [x] Указаны маска реквизитов и HMAC-SHA256.
- [x] Отделена проектная модель от фактических колонок CSV.
- [x] Создана и экспортирована диаграмма draw.io.

## 6. Каталог ошибок и результатов

Имя исходного файла 08_errors.md не соответствует его содержимому — ER-модели. Она сохранена; этот раздел добавлен, чтобы ссылки на каталог ошибок в остальных шаблонах имели адресат. HTTP-коды — проектное соглашение, а не результаты работающего сервера.

| Ситуация | Код | HTTP | Повтор |
|---|---|---|---|
| Неизвестный qr_id | QR_NOT_FOUND | 404 | нет без исправления ссылки |
| Подпись, структура или связь полей QR неверна | INVALID_QR | 422 | нет |
| exp < now | QR_EXPIRED | 410 | только с новым QR |
| QR занят другой попыткой или успешно использован | QR_USED | 409 | статус существующего платежа, без новой оплаты |
| Мерчант BLOCKED | MERCHANT_BLOCKED | 403 | нет |
| Мерчант PENDING | MERCHANT_NOT_ACTIVE | 403 | после активации |
| Чек запроса не совпадает с QR | RECEIPT_MISMATCH | 422 | после исправления |
| Чаевые вне 0–50% | TIP_OUT_OF_RANGE | 422 | после исправления |
| Чек или итог вне лимитов | AMOUNT_OUT_OF_RANGE | 422 | после исправления |
| Повтор ключа с теми же параметрами | нет ошибки | 200 | безопасен, возвращает прежний платёж |
| Повтор ключа с другими параметрами | IDEMPOTENCY_CONFLICT | 409 | нет с прежним содержимым ключа |
| Явный отказ шлюза, h=8 | CARD_DECLINED | 402 | новая попытка после устранения причины |
| Таймаут шлюза, h=9 | GATEWAY_TIMEOUT | 202 | проверить статус того же payment_id |
| Отмена не из CREATED | CANCEL_NOT_ALLOWED | 409 | нет |
| Неверный формат/обязательное поле/маска справочника | VALIDATION_ERROR | 422 | после исправления |
| Доступ к чужому объекту, неверный токен | FORBIDDEN | 403 | нет |
| Недопустимый переход статуса | INVALID_PAYMENT_STATE | 409 | запросить текущий статус |
| Неизвестный payment_id | PAYMENT_NOT_FOUND | 404 | нет без исправления |
| Превышен лимит запросов | RATE_LIMITED | 429 | после Retry-After |
| Сбой согласованного сохранения | INTERNAL_ERROR | 500 | проверить состояние, повторить с тем же ключом |

QR_NOT_FOUND, MERCHANT_NOT_ACTIVE, IDEMPOTENCY_CONFLICT, VALIDATION_ERROR, FORBIDDEN, INVALID_PAYMENT_STATE, PAYMENT_NOT_FOUND, RATE_LIMITED и INTERNAL_ERROR — проектные дополнения: исходный набор не давал полного каталога. Новый запрос с ошибкой до создания Payment не добавляет строку платежа.