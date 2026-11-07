# Data dictionary — кейс 3

## Состав словаря

Четыре файла: merchants (4 колонки), employees (4), payments (7), qr\_links (7).

Образцы merchants и payments сохранены; employees, qr_links и недостающие поля payments заполнены ниже.

## Образец merchants (не трогать)

| Файл | Поле | Тип | Обяз. | Ограничение | Пример | Код ошибки |
|---|---|---|---|---|---|---|
| merchants | merchant\_id | string | да | m\_NNN | m\_001 | — |
| merchants | name | string | да | 1–100 символов | Вкус-Сервис Тверская | — |
| merchants | status | enum | да | ACTIVE/BLOCKED/PENDING | ACTIVE | MERCHANT\_BLOCKED |
| merchants | description | string | нет | 0–200 | Ресторан сети | — |

## Образец payments (не трогать)

| Файл | Поле | Тип | Обяз. | Ограничение | Пример | Код ошибки |
|---|---|---|---|---|---|---|
| payments | payment\_id | string | да | p\_NNNN | p\_0001 | — |
| payments | amount | decimal | да | > 0, ≤ 1 000 000 | 637.00 | AMOUNT\_OUT\_OF\_RANGE |
| payments | tip | decimal | да | ≥ 0, ≤ amount × 0.5 | 63.70 | TIP\_OUT\_OF\_RANGE |
| payments | status | enum | да | CREATED/PENDING/SUCCESS/DECLINED/CANCELED | SUCCESS | — |

## employees

| Поле | Тип | Обяз. | Ограничение | Пример | Код ошибки |
|---|---|---|---|---|---|
| employee\_id | string | да | уникальный e_NNN, ссылка на сотрудника | e_001 | VALIDATION_ERROR |
| merchant\_id | string | да | m_NNN, существует в merchants | m_001 | VALIDATION_ERROR |
| name | string | да | 1–100 символов | Иванов И. | VALIDATION_ERROR |
| payment\_details | string | да | только маска: четыре звёздочки и четыре цифры | ****1001 | VALIDATION_ERROR |

## qr_links

| Поле | Тип | Обяз. | Ограничение | Пример | Код ошибки |
|---|---|---|---|---|---|
| qr\_id | string | да | уникальный qr_NNN | qr_001 | INVALID_QR |
| merchant\_id | string | да | m_NNN; FK на существующего мерчанта | m_001 | INVALID_QR |
| employee\_id | string | да | e_NNN; FK на сотрудника своего мерчанта; повторяется в разных QR | e_001 | INVALID_QR |
| receipt\_id | string | да | r_NNNN; уникален в пределах мерчанта; совпадает с запросом | r_1001 | RECEIPT_MISMATCH |
| nonce | string | да | 6 строчных hex; не занят другим платежом и не использован успешно | c82561 | INVALID_QR / QR_USED |
| exp | datetime UTC | да | ISO 8601; exp >= now; при выпуске TTL 24 часа | 2025-11-01T23:59:00Z | INVALID_QR / QR_EXPIRED |
| signature | string | да | первые 16 hex HMAC-SHA256; совпадает с расчётом | 68d0f361ac38c12b | INVALID_QR |

## Критерий приёмки

- [x] Все 4 файла описаны (22 колонки)

- [x] Для каждого поля указан тип и ограничение

- [x] Для полей с валидацией — код ошибки из 08\_errors.md

- [x] Новые примеры взяты из реального CSV; условные исходные образцы пояснены ниже

- [x] payment\_details — только маска (\*\*\*\*NNNN)

## Подсказки

- payment\_details — это реквизиты для выплат, не для оплаты.

- nonce — одноразовый, 6 hex.

- exp — время, после которого QR недействителен (обычно 24 ч).

- signature — HMAC-SHA256, 16 hex.

## Дополнение payments до полного состава CSV

Образцы выше сохранены. В payments.csv семь колонок; оставшиеся три описаны ниже.

| Файл | Поле | Тип | Обяз. | Ограничение | Пример | Код ошибки |
|---|---|---|---|---|---|---|
| payments | merchant_id | string | да | FK на merchants.merchant_id; согласован с QR и сотрудником | m_001 | VALIDATION_ERROR |
| payments | employee_id | string | да | FK на employees.employee_id; принадлежит мерчанту платежа | e_001 | VALIDATION_ERROR |
| payments | timestamp | datetime UTC | да | ISO 8601 с часовым поясом | 2025-11-01T18:00:00Z | VALIDATION_ERROR |

Всего описаны 22 колонки: 4 + 4 + 7 + 7. Новые примеры взяты из первых строк CSV. Образец tip=63.70 оставлен по указанию «не трогать»; в реальной строке p_0001 tip=31.85. Образец description также условный: у m_001 фактически «Ресторан сети, Тверская».

В таблице qr_links employee_id дополнительно должен принадлежать указанному merchant_id; нарушение связности QR — INVALID_QR. Для неверных ссылок merchant_id и employee_id в обычных справочниках применяется VALIDATION_ERROR; для входящего QR — INVALID_QR.

Файл 08_errors.md в исходном наборе содержит ER-модель, а не справочник ошибок. Единый проектный каталог кодов добавлен в его раздел 6; коды VALIDATION_ERROR, MERCHANT_NOT_ACTIVE и IDEMPOTENCY_CONFLICT явно обозначены там как дополнения.

Поля qr_id и idempotency_key отсутствуют в исходном payments.csv. Они относятся к проектной модели и должны храниться отдельно при реализации, без изменения входных CSV. Маска payment_details служит только для отображения, а не для реального перевода.

Все даты в исходных QR исторические. TTL 24 часа — правило генерации новых QR; фактическое время выпуска в CSV отсутствует, поэтому длительность их исходного TTL по одному exp подтвердить нельзя.