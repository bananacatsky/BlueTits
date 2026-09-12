BlueTits
========

BlueTits is a prototype commission marketplace powered by Privy and Arc Testnet.

Локальная разработка
--------------------

Backend и frontend запускаются отдельно:

```text
http://127.0.0.1:8000  Flask API
http://127.0.0.1:5173  Vite frontend
```

Установите frontend-зависимости один раз:

```bash
npm --prefix frontend install
```

Запустите оба сервера одной командой:

```bash
npm run dev:all
```

Или раздельно в двух терминалах:

```bash
python backend/server.py
npm --prefix frontend run dev
```

В локальном режиме Vite проксирует `/api/*` на Flask, поэтому CORS не нужен и
Flask session cookies продолжают работать как same-origin cookies.

Конфигурация
------------

Пример переменных находится в `.env.example`. При необходимости экспортируйте
их в окружение перед запуском. Основные переменные:

```text
BLUETITS_BACKEND_URL       backend для Vite proxy
VITE_API_URL               публичный API origin для production-сборки
BLUETITS_FRONTEND_ORIGINS  разрешённые origins для прямых cross-origin запросов
```

Если frontend обращается к API напрямую с другого origin, задайте точный origin:

```bash
export BLUETITS_FRONTEND_ORIGINS=http://127.0.0.1:5173
export VITE_API_URL=http://127.0.0.1:8000
```

Для разных production-сайтов cookies обычно потребуют HTTPS, а также
`BLUETITS_COOKIE_SAMESITE=None` и `BLUETITS_COOKIE_SECURE=1`.

Администрирование
-----------------

Корневой launcher оставлен для совместимости. Удалить пользователя вместе с
комиссиями и связанными заявками можно так:

```bash
python server.py deleteuser user@example.com
```

Публичный адрес профиля имеет вид `/@username`; email остаётся приватным.

Короткие deployment-команды
---------------------------

Собрать frontend, указав домен API:

```bash
./build-front.sh api.example.com
```

Запустить backend на выбранном порту:

```bash
./run-server.sh 8000
```
