# UK supply watch

Public page: https://hawkfry.github.io/uk-supply-watch/

A watch index for UK food-type risk, fuel prices, stocks and spare capacity. Higher is worse. It does not see the shop on your street.

Update the public page from this Mac:

```
python3 publish.py
```

That refreshes the feeds, then pushes the page. GitHub usually shows the new numbers within a minute.

The local board is still http://127.0.0.1:8765 after `python3 server.py`.
