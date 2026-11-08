# 0020. A personal page on a screen: a secret link, shown once, kept as a hash

- Status: accepted. The server side is in vigie-web (`src/lib/server/pageLinks.ts`); the television route that reads it
  (`/tv/p/<secret>`) belongs to the television work and is not part of this record's code.
- Date: 2026-11-08

## Context

"My page" (vigie-web, behind an account) holds the places a person follows and the cards they chose for each. The same people want that
page on a screen that has no keyboard to sign in with: a television, a tablet on a wall, the 7 inch display. A passkey cannot be used
there, and most television browsers do not keep a session for long.

The page is **personal**: its places are points kept to about 100 m, with names the person chose. A way to show it on a screen must not
make it public, must not reveal the home, and must be possible to take back.

## Options

1. **Sign in on the screen.** Needs a passkey or a code typed with a remote control. Rejected for now: most television browsers cannot do
   a passkey, and typing a long code is the thing the person wants to avoid.
2. **A pairing code** (the "device code" flow: the screen shows a short code, the person enters it on their phone). The right answer for a
   television that cannot be given a link, but it needs a table of pending codes and a page to enter them (migration 007). **Left for
   later**, not decided here.
3. **A public page per account.** Rejected: it would publish the places of everyone who wants a screen, which is what an account
   is meant to avoid.
4. **A secret link**, whose address holds a secret, opened by the screen without signing in. Chosen.

## Decision

1. **The secret** is 24 random bytes (192 bits) in base64url, 32 characters. It is shown **once**, when the link is made. The database
   keeps its **SHA-256 only**, like sessions and invitations, so a copy of the database holds no link anyone can open. To change a link,
   revoke it and make another.
2. **The address** is `/tv/p/<secret>`: the secret is in the path, not in the fragment (the server must read it). An access log would
   keep a path: the Caddy of the rented server writes none (`deploy/vps/Caddyfile`), and the example for the Deck skips this address
   (`log_skip /tv/p/*`).
3. **What a link shows**: the title of the page, its blocks **in the order the person set**, each with the name, point, radius and profile
   of its place, and the **units and language** of the account. Nothing else of the account: no name, no identifier, no e-mail.
4. **The home is never shown**, whatever the blocks say: the reader selects the places of kind `place` only, so a block that points to the
   home is dropped **when the page is read**, not only when it is saved. A page made of the home alone shows nothing, not an error.
5. **A link can be revoked** by its owner. A revoked link, an unknown one and a badly formed one all answer "not found" (`null`), with the
   same body as any other 404, so a revoked link cannot be told from one that never existed.
6. **A database that cannot answer is not "not found".** The reader throws, the route answers 503, and the screen keeps its last value and
   says it is offline. A screen must not blank itself because the database restarted.
7. **Reads are kept 60 seconds in memory**, by the hash of the secret (the answers for unknown secrets too, within a bound, so that a
   scan does not become a load). A revocation made by the same server is forgotten at once; one made through another server shows
   within a minute. The site's rate limit applies to the address as to any other.
8. **At most 10 active links per account**, counted under an advisory lock so that two requests cannot go past it. A revoked link frees its
   place and stays listed, as revoked.
9. **The date of the last opening is kept, at most once an hour, with no address**: enough to see that a screen still uses a link, which is
   what the owner needs to decide whether to revoke it.
10. **Headers**: every response under `/tv/p/` carries `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow` and
    `Cache-Control: private, no-store`, **set by the site's hooks** (`securityHeaders.ts`) and not left to the route, so that a route
    cannot forget them. No referrer even to this site: the requests the page makes for its data would carry the address in `Referer`,
    which an access log keeps. Everywhere else the policy stays `strict-origin-when-cross-origin`, and a page that asks for another one
    is still overwritten: honouring it was tried, and with `/join/<code>` (which asks for `no-referrer`) the end-to-end test that deletes an
    account failed with "Cross-site POST form submissions are forbidden". The cause was probably an `Origin: null` on a form posted by a
    document that began on the join page; it was not isolated further. The secret is never written to a log, an error message or a page
    title, and the page loads nothing from another site.

## What this does not do

- **A link is a key.** Whoever holds the address sees the page: a person looking at the screen, the history of the television's browser, a
  screenshot of the address bar. It is not a login. The interface that makes a link must say so, and say that the places shown are kept to
  about 100 m.
- **The home is protected by what the reader selects, not by the person's care**: it cannot be shown by mistake, but it can be *named* by
  the person in the title of the page. That is their text.
- **It does not know the screen.** A link opened by a second screen shows the same page; the owner sees only the date of the last opening.
- **No pairing code yet** (option 2), so a television that cannot be given an address by a phone still needs one typed.

## Consequences

- `page_links` (migration 006: SHA-256 of the secret, page, owner, label, dates of creation, last opening and revocation) already exists;
  `src/lib/server/pageLinks.ts` in vigie-web reads, makes, lists and revokes, and `personalPage.db.test.ts` checks the rules above against
  a real schema (the home never leaks, revoked and unknown secrets, another account's page, the limit, the cache, deletion with the page).
- `src/lib/server/securityHeaders.ts` holds the headers of the site, and the three of an address that holds a secret.
- A document keeps the referrer policy it was loaded with when the site navigates inside it. The television route should therefore
  leave the page by a full load (`data-sveltekit-reload` on its links), so that the next page does not inherit `no-referrer`.
- The records of processing (`docs/records-of-processing.md`) list the link's data and its retention.
- The television route reads `readSharedPage(sql, secret)` and nothing else of the account's data.
