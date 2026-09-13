BlueTits
========

BlueTits is a prototype commission marketplace powered by Privy and Arc Testnet.

Local development
-----------------

The backend and frontend run as separate development servers:

```text
http://127.0.0.1:8000  Flask API
http://127.0.0.1:5173  Vite frontend
```

Install the dependencies once:

```bash
npm --prefix frontend install
python -m pip install -r backend/requirements.txt
```

Create a local `.env` from the example and fill in the required values:

```bash
cp .env.example .env
```

Start both servers with one command:

```bash
npm run dev:all
```

Alternatively, start them in two terminals:

```bash
python backend/server.py
npm --prefix frontend run dev
```

In development, Vite proxies `/api/*` to Flask. Authentication uses a Privy
access token in the `Authorization` header, so the same mechanism works when
the frontend and API are hosted on different domains.

Configuration
-------------

The example configuration is in `.env.example`. The backend,
`npm run dev:all`, and Vite automatically read the root `.env`, so the values
do not need to be exported manually. Existing environment variables take
precedence over values from the file. The main settings are:

```text
BLUETITS_BACKEND_URL       backend URL used by the Vite development proxy
VITE_API_URL               public API origin embedded in a production build
BLUETITS_FRONTEND_ORIGINS  origins allowed to make cross-origin API requests
PRIVY_VERIFICATION_KEY     public ES256 verification key for the Privy app
```

When the frontend calls the API directly from another origin, configure the
exact origin:

```bash
export BLUETITS_FRONTEND_ORIGINS=http://127.0.0.1:5173
export VITE_API_URL=http://127.0.0.1:8000
```

For production, copy the verification key from the Privy Dashboard and allow
the exact GitHub Pages origin. The origin does not include `/BlueTits/`:

```bash
export PRIVY_VERIFICATION_KEY='-----BEGIN PUBLIC KEY-----
...
-----END PUBLIC KEY-----'
export BLUETITS_FRONTEND_ORIGINS=https://bananacatsky.github.io
```

A private key or Privy app secret is not required to verify access tokens.

Administration
--------------

Delete a user together with their commissions and related requests with:

```bash
python backend/server.py deleteuser user@example.com
```

Public profile URLs use the `/@username` format. Email addresses remain
private.

Deploying the frontend and backend on different domains
-------------------------------------------------------

The frontend can be hosted at `https://bananacatsky.github.io/BlueTits/` while
the API is hosted separately at `https://my-test-server.ru`. Authentication
works across these domains because the frontend sends a Privy access token in
the `Authorization` header; it does not depend on cross-site cookies.

### 1. Configure Privy

In the Privy Dashboard:

1. Add `https://bananacatsky.github.io` to the application's allowed origins.
   An origin does not include the `/BlueTits/` path.
2. Copy the public verification key from **Configuration → App settings →
   Basics → Verify with key instead**.

Keep the localhost origin in the list if local development must continue to
work.

### 2. Configure and start the backend

Install its dependencies and create `.env`:

```bash
python -m pip install -r backend/requirements.txt
cp .env.example .env
```

Use production values in `.env`:

```dotenv
HOST=127.0.0.1
PORT=8000
FLASK_DEBUG=0
BLUETITS_DB=/absolute/persistent/path/bluetits.sqlite3
PRIVY_APP_ID=cmtvduxb801d80bl8rg0vb7km
PRIVY_VERIFICATION_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
BLUETITS_FRONTEND_ORIGINS=https://bananacatsky.github.io
```

`BLUETITS_FRONTEND_ORIGINS` contains origins only, without paths or trailing
slashes. Multiple origins can be separated with commas.

For a quick deployment, start the API on the configured port:

```bash
./run-server.sh 8000
```

For a public service, run `backend.server:app` with a production WSGI server
and a service manager. Put an HTTPS reverse proxy such as Nginx or Caddy in
front of it, forwarding `https://my-test-server.ru/*` to
`http://127.0.0.1:8000/*`. HTTPS is required because an HTTPS GitHub Pages site
cannot call an HTTP API.

After starting the service, verify the public endpoint:

```bash
curl https://my-test-server.ru/api/health
```

It should return `{"ok":true}`.

### 3. Build the GitHub Pages frontend

From the repository root, pass the public API URL and the GitHub repository
base path to the build script:

```bash
./build-front.sh https://my-test-server.ru /BlueTits/
```

The build is written to `docs/`. The script also creates `docs/404.html` for
direct profile URLs such as `/BlueTits/@username`, and `docs/.nojekyll` for
GitHub Pages.

Commit and push the generated site:

```bash
git add docs
git commit -m "Build frontend for GitHub Pages"
git push
```

In the GitHub repository settings, configure Pages to deploy from the `docs/`
directory on the selected branch. Rebuild and commit `docs/` whenever the API
domain or frontend base path changes.

For a frontend hosted at the root of a custom domain, use `/` instead:

```bash
./build-front.sh https://my-test-server.ru /
```
