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
python -m pip install -r backend/requirements.txt
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

В локальном режиме Vite проксирует `/api/*` на Flask. Авторизация выполняется
Privy access token в заголовке `Authorization`, поэтому тот же механизм работает
и при размещении frontend и API на разных доменах.

Конфигурация
------------

Пример переменных находится в `.env.example`. При необходимости экспортируйте
их в окружение перед запуском. Основные переменные:

```text
BLUETITS_BACKEND_URL       backend для Vite proxy
VITE_API_URL               публичный API origin для production-сборки
BLUETITS_FRONTEND_ORIGINS  разрешённые origins для прямых cross-origin запросов
PRIVY_VERIFICATION_KEY     публичный ES256 verification key приложения Privy
```

Если frontend обращается к API напрямую с другого origin, задайте точный origin:

```bash
export BLUETITS_FRONTEND_ORIGINS=http://127.0.0.1:5173
export VITE_API_URL=http://127.0.0.1:8000
```

Для production скопируйте verification key из Privy Dashboard и разрешите
точный origin GitHub Pages (origin не включает `/BlueTits/`):

```bash
export PRIVY_VERIFICATION_KEY='-----BEGIN PUBLIC KEY-----
...
-----END PUBLIC KEY-----'
export BLUETITS_FRONTEND_ORIGINS=https://bananacatsky.github.io
```

Приватный ключ или Privy app secret для проверки access token не нужен.

Администрирование
-----------------

Удалить пользователя вместе с комиссиями и связанными заявками можно так:

```bash
python backend/server.py deleteuser user@example.com
```

Публичный адрес профиля имеет вид `/@username`; email остаётся приватным.

Короткие deployment-команды
---------------------------

Собрать frontend для GitHub Pages, указав домен API и base path:

```bash
./build-front.sh my-test-server.ru /BlueTits/
```

Результат записывается в `docs/`. Скрипт также создаёт `docs/404.html`, чтобы
прямые ссылки вида `/BlueTits/@username` работали на GitHub Pages.

Запустить backend на выбранном порту:

```bash
./run-server.sh 8000
```
