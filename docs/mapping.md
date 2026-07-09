# Маппинг Uzum Market → МойСклад

Схема прямая (без отчёта комиссионера). Модель FBO. Магазин Uzum: **ComFore** (shopId 387).
Склады МойСклад: физический **«Фулфилмент Евгений»** (источник), маркетплейс **«Узум»** (UUID обоих — в конфиге).

Все эндпоинты Uzum проверены живыми запросами 09.07.2026. Поля МойСклад — по официальной
документации JSON API 1.2 (github.com/moysklad/api-remap-1.2-doc, актуальный коммит).

## 1. Доступ к API

| | Uzum Seller API | МойСклад JSON API 1.2 |
|---|---|---|
| База | `https://api-seller.uzum.uz/api/seller-openapi` | `https://api.moysklad.ru/api/remap/1.2` |
| Авторизация | заголовок `Authorization: <токен>` — **без** `Bearer` | `Authorization: Bearer <токен>` |
| Обязательные заголовки | — | `Accept-Encoding: gzip` (иначе 415) |
| Лимиты | заголовки `x-ratelimit-*`, при 429 — `Retry-After` | 45 у.е./3 сек (отчёты остатков весят 5), 429 + `X-Lognex-Retry-After` |
| Пагинация | `page` (с 0), `size` (макс. 50) | `limit`/`offset` (макс. 1000) |

## 2. Источники данных Uzum (проверено)

| Данные | Эндпоинт | Ключевые поля |
|---|---|---|
| Заказы/продажи (FBO) | `GET /v1/finance/orders?shopIds={id}&page&size&dateFrom&dateTo&statuses` | строка = **позиция** заказа; `orderId` группирует. `dateFrom/dateTo` — **секунды**, `date`/`dateIssued` в ответе — миллисекунды |
| Поставки (накладные) | `GET /v1/shop/{shopId}/invoice?page&size` | `id`, `invoiceNumber`, `invoiceStatus.value` (CREATED/…/ACCEPTED), `totalToStock`, `totalAccepted`, `dateAccepted` |
| Состав поставки | `GET /v1/shop/{shopId}/invoice/products?invoiceId={id}` | по SKU: `skuTitle`, `quantityToStock` (отправлено), `quantityAccepted` (принято) |
| Товары и остатки FBO | `GET /v1/product/shop/{shopId}?page&size` | `skuList[]`: `skuId`, `skuTitle`, `barcode`, `quantityActive` (остаток на складе Uzum) |
| Возвраты со склада продавцу | `GET /v1/return?page&size` | вывоз товара со склада Uzum продавцу (не покупательские возвраты) — вне текущего скоупа |

Статусы позиции в `finance/orders`: `PROCESSING`, `TO_WITHDRAW`, `CANCELED`, `PARTIALLY_CANCELLED`.

⚠️ Факт по данным магазина: у всех SKU поля `article` и `sellerItemCode` — `null`.
Артикул продавца фактически лежит в **`skuTitle`** («PS-9908-1500МЛ»), но у части старых SKU
это просто название варианта («ЖЕЛТ», «БЕЛЫЙ») — неуникально между товарами.
Дополнительно у каждого SKU есть уникальный `barcode` Uzum (например `1000002762219`).

## 3. Заказ Uzum → Заказ покупателя (customerorder)

Строки `finance/orders` группируются по `orderId` → один Заказ покупателя.

| Uzum (`SellerOrderItemDto`) | МойСклад (`customerorder`) | Примечание |
|---|---|---|
| `orderId` | `externalCode` | ключ идемпотентности; поиск `?filter=externalCode=...` |
| `orderId` | `name` = `UZ-{orderId}` | видимый номер |
| `date` (мс) | `moment` | перевод в московское время (формат МС) |
| — | `organization` | из конфига (первая организация аккаунта) |
| — | `agent` | единый контрагент **«Uzum Market (покупатель)»** — FBO не раскрывает покупателя; создаётся при первом запуске |
| — | `store` | склад «Узум» |
| позиция: `skuTitle` | `positions[].assortment` | матчинг по артикулу (см. §7) |
| позиция: `amount` | `positions[].quantity` | |
| позиция: `sellPrice` (сум) | `positions[].price` | ×100 (МС хранит цены в минорных единицах) |
| `orderId` | атрибут «ID заказа Uzum» | строка |
| `dateIssued` (мс) | атрибут «Дата доставки» | тип time; заполняется при выкупе |
| Σ `commission` по позициям | атрибут «Комиссия» | double, в сумах |
| Σ `logisticDeliveryFee` | атрибут «Логистика» | double, в сумах |

## 4. Статусы → триггеры документов

| Статус позиций Uzum | Действие в МойСклад | Статус заказа МС |
|---|---|---|
| `PROCESSING` (новый заказ) | создать Заказ покупателя (склад «Узум», резерв позиций) | «Uzum: в обработке» (Regular) |
| `TO_WITHDRAW` (выкуплен, `dateIssued` заполнен) | создать **Отгрузку** (demand) со склада «Узум», `customerOrder` → заказ, позиции = выкупленные; атрибуты продублировать | «Uzum: выкуплен» (Successful) |
| `CANCELED` (все позиции) | отмена: статус Unsuccessful + `applicable=false` (снимает резерв). Отгрузка не создаётся | «Uzum: отменён» (Unsuccessful) |
| `PARTIALLY_CANCELLED` / часть позиций `CANCELED` | уменьшить/удалить отменённые позиции заказа (комментарий об отмене), отгрузка позже — на остаток | «Uzum: в обработке» |
| `amountReturns > 0` у позиции (после выкупа) | создать **Возврат покупателя** (salesreturn) на основании отгрузки: склад «Узум», количество = `amountReturns`, причина `returnCause` → описание | «Uzum: возврат» (Regular) |

Статусы «Uzum: …» сервис ищет по имени в метаданных customerorder и создаёт через API при первом запуске (как и атрибуты, §6).

Дельта возвратов отслеживается в SQLite (синхронизировано N из `amountReturns`), повторный запуск возврат не дублирует.

## 5. Поставка Uzum → Перемещение (+ Списание при недостаче)

| Uzum | МойСклад | Примечание |
|---|---|---|
| накладная `invoiceStatus.value = CREATED` | **Перемещение** (move): `sourceStore` = «Фулфилмент Евгений», `targetStore` = «Узум» | `name = UZ-INV-{invoiceNumber}`, `description` со ссылкой на накладную |
| позиция: `quantityToStock` | `positions[].quantity` | матчинг по `skuTitle` (§7) |
| `id` накладной ↔ id перемещения | SQLite `invoice_map` | идемпотентность |
| приёмка завершена: `dateAccepted` заполнен (или статус ACCEPTED) | сверка `quantityAccepted` vs `quantityToStock` по каждому SKU | пары отправлено/принято сохраняются в SQLite |
| недостача (`accepted < sent`) | **Списание** (loss) со склада «Узум» на разницу, `description` = «Расхождение приёмки поставки №{invoiceNumber}: отправлено N, принято M» | + уведомление в Telegram |

Логика: перемещение делается сразу на отправленное количество; если склад принял меньше —
списание корректирует остаток «Узум» до фактически принятого.

## 6. Доп. поля (attributes)

При старте сервис проверяет `GET /entity/{тип}/metadata/attributes` и создаёт недостающие
(`POST` массивом) для **customerorder** и **demand**:

| Название | Тип МС | Источник |
|---|---|---|
| ID заказа Uzum | `string` | `orderId` |
| Дата доставки | `time` | `dateIssued` |
| Комиссия | `double` | Σ `commission` |
| Логистика | `double` | Σ `logisticDeliveryFee` |

## 7. Матчинг товаров

Порядок поиска позиции в МойСклад (кэш в SQLite):

1. `GET /entity/product?filter=article={skuTitle}` — артикул товара = `skuTitle` Uzum;
2. если не найден — `GET /entity/variant` по коду/характеристикам (модификации);
3. если не найден — `GET /entity/assortment?filter=barcode={barcode}` — если штрихкод Uzum занесён в карточку МС;
4. не найден нигде → документ **не создаётся**, запись в лог + уведомление в Telegram
   (повтор на следующем цикле, чтобы можно было завести карточку и досинхронизировать).

## 8. Сверка остатков (раз в час)

| Uzum | МойСклад |
|---|---|
| `skuList[].quantityActive` (по всем товарам магазина) | `GET /report/stock/bystore/current?stockType=stock` по складу «Узум» (лёгкий эндпоинт) |

Расхождение по любому SKU → сводное уведомление в Telegram (без автокоррекции документов).

## 9. Идемпотентность (SQLite)

| Таблица | Ключи |
|---|---|
| `order_map` | `uzum_order_id` ↔ `ms_order_id`, статус, `demand_id`, `returned_qty_synced` по позициям |
| `invoice_map` | `uzum_invoice_id` ↔ `ms_move_id`, `ms_loss_id`, снимок sent/accepted, флаг сверки |
| `sku_cache` | `skuTitle`/`skuId` ↔ `ms_assortment_href` |
| `state` | точка последнего опроса (`dateFrom` очередного цикла с перекрытием) |

## 10. Открытые вопросы (на согласование)

1. **Матчинг**: подтвердить, что артикулы в МойСклад совпадают с `skuTitle` Uzum
   (например «PS-9908-1500МЛ»). Для старых SKU с названиями типа «ЖЕЛТ» потребуется
   либо занести штрихкод Uzum в карточку МС, либо ручная таблица соответствий.
2. **Контрагент**: один общий «Uzum Market (покупатель)» на все заказы — ок?
3. **Частичная отмена**: уменьшаем позиции заказа и отгружаем остаток — ок?
4. **Цены**: аккаунт МойСклад в сумах (UZS)? Цены передаём ×100 (минорные единицы МС).
5. Возвраты со склада Uzum продавцу (`/v1/return`, вывоз товара) — сейчас вне скоупа;
   при желании позже добавим Перемещение «Узум» → «Фулфилмент Евгений».
