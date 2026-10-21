# Calma extension

The Chromium MV3 companion of the Calma daemon. Plain JS, no build step, no dependencies.
It is loaded for every Chromium through `--load-extension=/usr/share/calma/extension`
(`/etc/chromium-flags.conf`, kept in place by the daemon). The daemon's managed policy does
the actual blocking; the extension makes it pleasant:

- the **blocked page** (`blocked.html`), themed from the Omarchy theme via `GET /theme`,
  with a "Start a session" button that calls `POST /open`;
- a **countdown pill** on rule domains while a session runs, with "End now";
- **minimal versions** of X (`content/x.js`: Following only, no sidebar, no Explore/Grok/
  Premium, no promoted posts, no "Discover more") and YouTube (`content/youtube.js`: no home
  feed, no Shorts, no related videos, no end screens, no comments, autoplay off, `/` and
  `/shorts/*` go to Subscriptions);
- **instant reaction**: a long-poll on `GET /events` redirects open tabs to the blocked page
  the moment a session ends, and `declarativeNetRequest` dynamic rules redirect blocked
  domains before any request leaves the browser.

## Files

| File | What |
|---|---|
| `manifest.json` | MV3 manifest; permissions `tabs, webNavigation, declarativeNetRequest, alarms, storage` |
| `background.js` | Service worker: status cache, `/events` long-poll, `/ping` on start and every 30 s, DNR rules, tab closing, badge (`m:ss`), messages |
| `blocked.html`, `blocked.js`, `blocked.css` | The blocked page (`blocked.html?rule=<name>&url=<original>`) |
| `content/countdown.js`, `.css` | The countdown pill, on `<all_urls>`, active only on rule domains |
| `content/x.js`, `.css` | Minimal X |
| `content/youtube.js`, `.css` | Minimal YouTube |
| `icons/` | The leaf, rendered from `site/favicon.svg` (`rsvg-convert -w N -h N site/favicon.svg -o extension/icons/iconN.png`) |
| `dev/mock-daemon.py` | A stdlib mock of the daemon API for development |

## How it fails

Closed. The DNR rules persist across service-worker restarts and the last status is kept in
`chrome.storage.session`. When the daemon has been unreachable for more than 15 s every rule
is treated as blocked, tabs on rule domains go to the blocked page ("Calma isn't running"),
and the content scripts keep their cosmetic rules on regardless. The managed policy layer
still applies underneath.

## Messages (content scripts and the blocked page → service worker)

| Message | Reply |
|---|---|
| `{type: "status"}` | `{ok, status, theme, daemonDown, receivedAt}` |
| `{type: "theme"}` | `{ok, theme}` |
| `{type: "open", rule, minutes?}` | the `POST /open` body plus `httpStatus` |
| `{type: "close", rule}` | the `POST /close` body plus `httpStatus` |
| `{type: "ruleFor", url?}` | `{ok, rule}`: the rule owning the URL's host, or null |

## Developing

```sh
python3 extension/dev/mock-daemon.py 7390            # x open, youtube blocked, steam exhausted
python3 extension/dev/mock-daemon.py 7390 --theme light
python3 extension/dev/mock-daemon.py 7390 --all-blocked --cooldown 10

chromium --user-data-dir=/tmp/calma-ext-dev --load-extension="$PWD/extension" https://www.youtube.com/
```

`blocked.html` also works opened as a plain file (`blocked.html?rule=youtube`): outside the
extension it talks to the daemon on `127.0.0.1:7390` directly, which makes design work easy.

Checks: `node --check extension/background.js extension/blocked.js extension/content/*.js`
and `python3 -c 'import json; json.load(open("extension/manifest.json"))'`.
