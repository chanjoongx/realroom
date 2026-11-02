# Security

## Reporting a problem

Please report security problems privately: open the Security tab of this repository and choose "Report a vulnerability". Please don't open a public issue for them.

A useful report says what you sent, what came back and which commit or address you used. Please don't start live runs on the hosted demo to test a finding, since each one is paid for. A local server started with `--live` and your own key runs the same code (see [Run it](README.md#run-it)).

## The hosted demo

The Meta Model API key is in a Modal secret that holds that key and nothing else. Only the server reads it. It never reaches the browser, a run file or the log.

Live runs are capped at 40 a UTC day, 8 an hour, three per visitor, six per IPv6 /48 and $60 of model calls in all. Visitors are counted by a salted hash of their address, and the salt changes every UTC day. Every Muse Spark answer is capped at 16,384 tokens. A run that passes 12 minutes or $0.60 skips the redraws it has left.

Uploads must be JPEG, PNG or WebP, at most 12 MB and 40 megapixels. The server reads the pixel count from the file header before it decodes anything, then re-encodes the photo without its metadata. A live run's files sit at an address with 96 random bits, are sent with `Cache-Control: private, no-store` and are deleted 18 hours after the upload.

Live runs can only be started from the Realroom page itself: the server checks `Sec-Fetch-Site` and `Origin`. Every response carries a Content-Security-Policy with `default-src 'self'` and `frame-ancestors 'none'`.

## Model output

The visitor's brief goes into the Muse Spark prompt between `<brief>` tags, with any angle brackets removed, and the prompt says the brief cannot change the rules or the photo check. Answers are parsed against a JSON schema, numbers and list indexes are range-checked and text fields are cut to 600 characters. No model text becomes a file name, a URL or HTML, and the page escapes everything it shows.

## Dependencies

CI installs the test dependencies from `backend/requirements-test.lock` with `pip --require-hashes`, runs GitHub Actions pinned to commit SHAs with read-only permissions and installs the web app with `npm ci` from `package-lock.json`.
