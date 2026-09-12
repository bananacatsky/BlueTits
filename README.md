BT
====

BT is a prototype platform for artists to sell commissions, powered by Privy with a fiat on-ramp and built on the Arc Testnet.

Удалить пользователя вместе с его комиссиями и связанными заявками можно из каталога проекта:

```bash
python server.py deleteuser user@example.com
```

Новые профили настраиваются после первого входа: публичный адрес имеет вид
`/@username`, email остаётся приватным, а в профиле отображаются username и wallet.
