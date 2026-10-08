# Fiboki data sources: API keys (Finnhub, FRED) and a catalogue of free sources

Researched 8 October 2026. UK English. Everything below was read or tested in this session unless it carries the tag **[not verified]**.

Tags used:
- **[read]** the official page was read in full or the quoted clause was read verbatim.
- **[live]** a single GET to the endpoint returned HTTP 200 from this environment (no account, no key).
- **[not verified]** I could not read the page or confirm the claim. Nothing in this file states such a claim as fact.

I did not create any account, enter any email or password, or submit any form. The two sign-up steps in section A are for you to do.

---

## 0. What the repository already has, and three things to fix

Read: `src/fiboki/data/sources/registry.py` (via `describe_sources()`, 34 entries), `src/fiboki/data/news/sources.py`, `src/fiboki/api/settings.py`, `docs/v2/DATA_ARCHITECTURE.md` section 14, `USER_ACTIONS.md` P6.

Note: the news code lives in `src/fiboki/data/news/`, not `src/fiboki/news/`.

Already in the registry (so not repeated below as new): Fed, ECB, BoE, BoJ, SNB and RBA RSS; BIS central bankers' speeches; Finnhub news; Marketaux; GDELT DOC 2.0; the committed official calendar fixture; Finnhub calendar (premium); ForexFactory feed (opt-in); CFTC COT; OANDA books; Myfxbook outlook; ALFRED; the FRED cross-asset pack; ECB SDMX; BoE IADB; ONS; NY Fed; HistData; Dukascopy; OANDA candles. Registry entries marked forbidden: Investing.com, TradingView undocumented endpoints, Myfxbook and ForexFactory scraping, Reuters, AP.

Environment variable names the code actually reads (grep of `src/fiboki`):

| Source | Variable | Where read |
|---|---|---|
| Finnhub | `FIBOKI_FINNHUB_API_KEY` | `data/news/sources.py:82`, `data/providers/finnhub.py:62`, `api/settings.py:230` |
| FRED / ALFRED | `FIBOKI_FRED_API_KEY` | `data/providers/alfred.py:56`, `api/settings.py:266` |
| Marketaux | `FIBOKI_MARKETAUX_API_KEY` | `data/news/sources.py:83`, `api/settings.py:253` |
| GDELT (opt-in flag, no key) | `FIBOKI_GDELT_ENABLED` | `data/news/sources.py:84`, `api/settings.py:234` |

Findings to act on:

1. **`USER_ACTIONS.md` line 280 uses the wrong names.** It says "Still to add: `FIBOKI_FINNHUB_KEY`, `FIBOKI_FRED_KEY` (P6)". The code reads `FIBOKI_FINNHUB_API_KEY` and `FIBOKI_FRED_API_KEY` (and P6 further up the same file gets it right). Nothing in `src/` reads the `_KEY` spellings. If you follow line 280 literally both sources stay silently off. I have not edited the file (research brief only).
2. **The FRED notice is a constant, not something an operator sees.** `FRED_ATTRIBUTION` exists in `data/providers/alfred.py:61`, but a grep of `src/` and `apps/` (excluding `node_modules`) finds no use of the text in the web UI. FRED's terms say you "must" place it "prominently on your application" (quoted in A2). `GDELT_ATTRIBUTION` likewise exists only as a constant. If FRED or GDELT data is ever shown in the operator UI, the notice needs a footer.
3. **`~/.fiboki/env` is read only by the launchd wrapper and by `fiboki doctor`.** `scripts/fiboki-service.sh` loads it line by line (no shell expansion). A `fiboki macro fetch alfred ...` typed in your own terminal does not load it, because `cli.py` does not call `read_env_file` for that command. For a one-off fetch export the variable first (see A2). After editing the file, restart the service that needs it (command in A3).

A fourth, structural point: the headline store's `NewsSource` enum (`data/news/store.py:61`) has `FED_RSS ... RBA_RSS, FINNHUB, MARKETAUX, OTHER` and a `CHECK` constraint in existing store files (DATA_ARCHITECTURE section 14.5). New central-bank feeds (Bank of Canada, RBNZ, Norges Bank, Riksbank) would be stored as `OTHER` as BIS is, unless you accept a store migration.

---

## A. The two keys

### A1. Finnhub (free, personal use)

**Steps**

1. Open https://finnhub.io/register [read]. The page is titled "Get your free API key" and has three fields (Name, Email, Password) and a "Sign Up" button. I saw no Google or GitHub sign-in button on it.
2. Read the sentence above the button first (there is no tick box; pressing Sign Up is the acceptance): "By signing up with Finnhub, you understand that you have read and understand our terms of service. You hereby confirm that you are either a qualified non-professional (personal use) under our terms or a commercial/professional user with our written approval." Sign up as the personal-use user.
3. Complete any email verification Finnhub asks for. I did not sign up, so I cannot say whether it sends one **[not verified]**.
4. Sign in at https://finnhub.io/login. Your key is on https://finnhub.io/dashboard (that URL redirects to the login page when signed out [live]). Finnhub's own API description says "You can find your API Key under Dashboard" and that requests carry the key either as `token=apiKey` in the URL or in a header `X-Finnhub-Token : apiKey` (from https://finnhub.io/static/swagger.json [read]). Fiboki uses the header, which keeps the key out of URLs and logs.
5. Put the key in `~/.fiboki/env` (A3). Do not paste it into chat, the repository, or a shell history line.

**Free-tier limits** (https://finnhub.io/pricing, read in a browser, free "All-In-One" column):

- Price "$0/month". Licence row: "Personal Use. Terms apply".
- "Limit 60 API calls/minute" (the premium column shows "Market data: 900 API calls/minute" and "Fundamental: 300 API calls/minute"). This confirms the figure the registry took from the brief.
- API documentation (swagger "Limits" section [read]): "If your limit is exceeded, you will receive a response with status code 429. On top of all plan's limit, there is a 30 API calls/ second limit."

**Free versus premium, for the endpoints you asked about**

| Endpoint | Free? | Evidence |
|---|---|---|
| `GET /api/v1/news?category=forex` and `category=general` (also `crypto`, `merger`; `minId` for paging) | Yes. No premium flag on the endpoint. | swagger `/news`: `"premium": null`; categories text "general, forex, crypto, merger" [read] |
| `GET /api/v1/calendar/economic` | **No.** | Pricing table shows a cross against "Economic Calendar" and "Historical Economic Data" in the free column [read, screenshot]; swagger says `"premium": "Premium Access Required"` and "Historical events and surprises are available for Enterprise clients." |
| `/forex/candle`, `/forex/rates` | No (Premium Access Required) | swagger [read] |
| `/news-sentiment`, `/index/constituents` | No (Premium Access Required) | swagger [read] |
| `/company-news` | Yes, 1 year of history on free | pricing table row "Company News 1 year and real-time updates" |

So the free key gives you exactly what the registry's `finnhub_news` entry uses (forex and general headlines) and nothing for the calendar. This matches the registry.

**Terms points for storing headlines**

- I could not read the terms of service. https://finnhub.io/robots.txt says `Disallow: /terms-of-service` and `Disallow: /faq` (the registry recorded the same refusal). I fetched that page once before reading robots.txt, then discarded it unread rather than rely on a page the site asks automated tools not to read. **Read it yourself in a browser before you rely on any storage right.**
- What is on pages I could read: the free plan is licensed "Personal Use. Terms apply", and registration requires you to confirm you are a "qualified non-professional (personal use)".
- Whether storing headlines for research is allowed, and whether any display notice is required, are **[not verified]**. The recorder already stores headline, URL and metadata only, which is the conservative reading. Do not republish or show Finnhub items to anyone else.

### A2. FRED (St. Louis Fed), with ALFRED vintages on the same key

**Steps**

1. Go to https://fredaccount.stlouisfed.org/login/secure/ [seen in a browser]. It offers "Sign In" and "Create New Account" tabs, and "Sign in with Google". The Create New Account tab asks for Email Address, Password, Confirm Password, optional newsletter tick boxes (Economic Research, FRED, FRED in the Classroom, Federal Reserve Education, FRASER) and a drop-down "In what context do you use FRED?". Untick the newsletters if you do not want mail. Click "Create Account".
2. Confirm the email if FRED sends a verification message **[not verified]**.
3. While signed in, open https://fredaccount.stlouisfed.org/apikeys. The API key page (https://fred.stlouisfed.org/docs/api/api_key.html [read]) says you "cannot request or view your API keys without first logging into" your account, and that you request a key per application. Request one for Fiboki.
4. The key is "a 32 character lower-cased alpha-numeric string" (same page [read]). It stays visible on the same `apikeys` page after you log in.

**Limits**

- `https://fred.stlouisfed.org/docs/api/fred/errors.html` [read via fetch]: "Up to 120 requests per minute are allowed before being served a 429 error code." and "Not complying with the throttling can result in a temporary block."
- No daily quota or fee is stated on the key page.
- Real-time (vintage) parameters: `series/observations` accepts `realtime_start`, `realtime_end` and `vintage_dates`; `limit` defaults to 100000. This is what ALFRED provider `alfred.py` relies on.
- Release dates: `fred/release/dates` exists; its description notes the dates "may differ from when data appears on FRED or ALFRED", and `include_release_dates_with_no_data` defaults to false (so future dates are excluded unless set).

**Licence and notice** (https://fred.stlouisfed.org/docs/api/terms_of_use.html, read in full in a browser):

- Required notice, under "Requirements": "Place the following notice prominently on your application: 'This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis.'"
- Third-party series, under "Property Rights": "Data series available through the FRED® API, may be owned by third parties and subject to copyright restrictions." and "Before using data series owned by third parties for anything other than your own personal use, you must contact the data owner to obtain permission." and "Copyrighted series contain the word 'Copyright' in their notes." (You can search the series notes for "copyright". The registry already treats VIXCLS this way.)
- Prohibitions include: using the API for an application that "replicates or attempts to replace the essential user experience of the FRED® API, or the FRED® or ALFRED® web sites"; using "an unreasonable amount of bandwidth"; and removing "proprietary rights notices ... that may be affixed to data accessed or provided through the FRED® API."
- Limits clause: the St. Louis Fed "may impose or adjust the limit on the amount of bandwidth you may use or the number of transactions you may send or receive".
- **There is no clause on storing or caching FRED data locally for your own research.** The only storage language is the destroy-on-termination clause for the API software itself. Storing public-domain series (DGS2, DGS10, DTWEXBGS) for personal research is therefore not restricted by these terms; copyrighted series are the exception, as above.
- You may not use "FRED", "ALFRED" or "Federal Reserve Bank" in the hostname of your application (the terms give FRED.mydomain.com as the example).

### A3. The lines to add, and how to apply them

`~/.fiboki/env` format (from `core/env_file.py`): `KEY=VALUE`, split at the first `=`, no quotes needed, no shell expansion, `#` comments ignored, `chmod 600`.

```
FIBOKI_FINNHUB_API_KEY=<your Finnhub key>
FIBOKI_FRED_API_KEY=<your 32 character FRED key>
```

Apply:

- Headline recorder (Finnhub): `launchctl kickstart -k gui/$(id -u)/uk.fiboki.news` (pattern from `docs/v2/DEPLOYMENT.md` line 81, which shows it for `uk.fiboki.worker`). The recorder reads the key once at start. The 60 calls per minute cap is enforced client-side.
- One-off FRED fetch from a terminal (the CLI does not read the env file): `export FIBOKI_FRED_API_KEY="$(grep '^FIBOKI_FRED_API_KEY=' ~/.fiboki/env | cut -d= -f2-)"` then `fiboki macro fetch alfred --series DGS10 ...`. Do not `source` the file: the scrypt hashes contain `$` (the wrapper script says so).
- Check with `fiboki doctor` afterwards.

Optional lines the code already understands: `FIBOKI_MARKETAUX_API_KEY=<token>` and `FIBOKI_GDELT_ENABLED=true`.

---

## B. Catalogue of sources beyond the registry

Order: central banks and meeting calendars, statistics offices and release calendars, data APIs, vendor APIs, then a source that forbids automated access. "Primary" means the publisher itself; "aggregator" means a third party repackaging.

Latency remarks are my assessment from how each source publishes; **I did not measure latency for any of them**.

### B1. Central banks and meeting calendars

**1. Bank of Canada: RSS and Valet API** (primary)
- Gives: press releases, speeches, news, Monetary Policy Report, summary of deliberations, market notices; Valet API for FX (e.g. `FXUSDCAD`) and policy-rate series.
- Access: RSS, REST/JSON. Feeds listed at https://www.bankofcanada.ca/rss-feeds/ [read]: `https://www.bankofcanada.ca/content_type/press-releases/feed/` [live], `.../content_type/speeches/feed/`, `.../utility/news/feed/`, `.../content_type/mpr/feed/`, `.../content_type/summary-of-deliberations/feed/`. The page does not say which feed carries rate decisions; check press-releases first. Valet: `https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1` [live]; docs https://www.bankofcanada.ca/valet/docs.
- Cost: free; no key found.
- Terms (https://www.bankofcanada.ca/terms/ [read]): "the Bank permits you to freely use, copy, distribute and transmit its website content under the following terms". "You must attribute the Bank of Canada as the source of the content, and indicate if changes were made." Users may not "Circumvent such limit or limits imposed by the Bank with respect to the number or frequency of requests made to a Bank Site, including the retrieval of financial data and information using Bank of Canada services (e.g., the Bank of Canada Valet API )." No numeric limit is published that I could find.
- Use: fills the CAD gap in the 13-feed set; Valet gives clean daily FX fixings and the overnight rate. Press feed latency is minutes; Valet is daily.

**2. Reserve Bank of New Zealand: news RSS** (primary)
- Gives: news releases (OCR decisions, monetary policy statements, appointments).
- Access: RSS 2.0 `https://www.rbnz.govt.nz/feeds/news`, title "Reserve Bank of New Zealand News Releases" [live]. The feeds index page https://www.rbnz.govt.nz/rss-feeds answers 403 to scripts, so I could not list other feeds [not verified]; I did not guess any.
- Cost: free.
- Terms: copyright and reuse page not retrieved [not verified]. Treat as personal-only until read.
- Use: NZD events; no RBNZ feed exists in the registry.

**3. Norges Bank: open data API** (primary)
- Gives: exchange rates, interest rates including the policy rate and Nowa, government securities.
- Access: REST data warehouse at https://query.norges-bank.no/en/, described at https://www.norges-bank.no/en/topics/statistics/open-data/ [read]. Base URL and format list are not on that page [not verified]. The RSS page `https://www.norges-bank.no/en/rss/` returned 404, so no RSS URL is confirmed.
- Cost: free; key requirement not stated.
- Terms: the data.norge.no catalogue entry lists licence "NLOD 2.0" (Norwegian Licence for Open Government Data) for the Norges Bank open data set; I did not read the licence text. Contact on the page: data@norges-bank.no.
- Use: NOK policy rate and EURNOK fixings. Low frequency.

**4. Sveriges Riksbank: RSS and API** (primary)
- Gives: press releases, notices, calendar events, speeches, minutes of monetary policy meetings; API for interest rates and exchange rates.
- Access: RSS (listed at https://www.riksbank.se/en-gb/press-and-published/subscribe-via-rss/ [read]): `/en-gb/rss/press-releases/` [live], `/en-gb/rss/notices/`, `/en-gb/rss/calendar/`, `/en-gb/rss/speeches/` [live], `/en-gb/rss/minutes-of-the-executive-boards-monetary-policy-meetings/` (all under https://www.riksbank.se). REST API: https://developer.api.riksbank.se/ and https://www.riksbank.se/en-gb/statistics/interest-rates-and-exchange-rates/retrieving-interest-rates-and-exchange-rates-via-api/.
- Cost: free. The page says: "You can use the API free of charge. The number of calls that may be made during a period of time from a given IP address is limited"; registering at the developer portal gives higher limits. The numeric limit was not on the pages I could read [not verified].
- Terms for content reuse: not read [not verified].
- Use: SEK events. The `calendar` feed is a useful machine-readable list of Riksbank meetings.

**5. Bank of Japan: Time-Series Data Search API** (primary)
- Gives: BoJ statistical series as JSON or CSV. Announced in https://www.boj.or.jp/en/statistics/outline/notice_2026/not260218a.htm (dated 18 February 2026) [read].
- Access: REST. Portal https://www.stat-search.boj.or.jp/index_en.html; manual https://www.stat-search.boj.or.jp/info/api_manual_en.pdf; notice https://www.stat-search.boj.or.jp/info/api_notice_en.pdf [both read].
- Cost: free. Manual limits per request: 250 series codes, 60,000 data points (output is cut at the limit and a resume position is given).
- Terms (API notice [read]): "The Bank may limit access depending on the load status of the API." Prohibited: "Excessive access frequency or other acts that interfere with the operation of the API". If you release a service that uses the API, notify post.rsd17@boj.or.jp and show: "This service uses the API provided by the "Bank of Japan Time-Series Data Search." The Bank of Japan does not guarantee the content of the service." Caution: the registry marks BoJ content `personal_only` because the general copyright page bars commercial copying; the API notice does not override that.
- Use: yen series (policy rate, JGB yields, monetary base). Monthly or daily; not an event clock.

**6. Federal Reserve Board: data release RSS** (primary; extends registry `fed_rss`)
- Gives: new feeds beyond the three already recorded: H.15 selected interest rates `https://www.federalreserve.gov/feeds/h15.xml`; foreign exchange rates G.5/H.10 `https://www.federalreserve.gov/feeds/h10.xml` [live]; industrial production G.17 `https://www.federalreserve.gov/feeds/g17.xml`; Data Download Program announcements `https://www.federalreserve.gov/feeds/datadownload.xml`. Listed at https://www.federalreserve.gov/feeds/feeds.htm [read]; per-series H.10 feeds at https://www.federalreserve.gov/feeds/h10_data.htm.
- Cost: free, no key.
- Terms: the registry already records the Fed disclaimer: "Unless otherwise indicated, information on Board's website is in the public domain and may be copied and distributed without permission. Please cite to the Board" (read 2026-09-29 by the earlier session).
- Use: the feeds are release announcements (timestamped notices that a release is out), a cheap trigger to pull the values. There is no dedicated FOMC feed; `press_monetary.xml` (already recorded) covers it.

**7. Fed FOMC meeting calendar** (primary, HTML)
- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm [live, 200]. A page of dates, no iCal or JSON found in the page text.
- Cost: free. Terms as item 6. Use: cross-check for the committed official calendar fixture (the registry says that fixture is refreshed by hand). Parse weekly at most.

**8. ECB monetary policy meeting calendar** (primary, HTML)
- https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html [live, 200]. No machine-readable export confirmed.
- Terms as registry `ecb_rss`: free use citing the ECB. Use: fixture cross-check.

**9. Bank of England MPC dates** (primary, HTML)
- https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates [read]: "Monetary Policy Committee dates for 2026 and 2027", with 2026 confirmed dates listed.
- Terms: Open Government Licence v3.0 per the registry. Use: fixture cross-check.

**10. Bank of Japan monetary policy meeting schedule** (primary, HTML)
- https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm [live, 200]. Terms as registry `boj_rss` (personal only). Use: fixture cross-check.

### B2. Statistics offices and release calendars

**11. US BLS Public Data API v2** (primary)
- Gives: employment situation, CPI, PPI, JOLTS and other BLS series by series ID (you must know the IDs; BLS publishes no catalogue).
- Access: REST (POST or GET) at `api.bls.gov` (host named in the FAQ). Register at https://data.bls.gov/registrationEngine/ with email and organisation name plus a CAPTCHA (a human step); the key arrives from `labstat@bls.gov`. "Users must renew registration ... at least once a year." (https://www.bls.gov/developers/api_faqs.htm [read])
- Limits (v2, registered): 500 queries per day, 50 series per query, 20 years per query, "50 requests per 10 seconds". Unregistered v1: 25 per day.
- Terms (https://www.bls.gov/developers/termsOfService.htm [read]): "Data accessed through BLS.gov do not, and should not, include controls over its end use." Users must cite the access date and state "BLS.gov cannot vouch for the data or analyses derived from these data after the data have been retrieved from BLS.gov." "Users may not modify or falsely represent content accessed through BLS.gov and still cite the source as BLS.gov." The BLS logo may not be used. Access may be blocked if BLS "reasonably believes that a user has attempted to exceed or circumvent these limits".
- Practical: `www.bls.gov` pages returned 403 to a plain scripted GET but loaded in a browser; test the API host with your own User-Agent before relying on it.
- Use: NFP and CPI are the highest-impact USD and gold events. Latest values are the first print; vintages need ALFRED.

**12. BLS release schedule** (primary)
- https://www.bls.gov/schedule/news_release/ ; iCal subscription `https://www.bls.gov/schedule/news_release/bls.ics` (the page offers it for Google Calendar, Outlook and Apple Calendar). All times Eastern (the page lists Employment Situation at 8:30 AM ET). The `.ics` URL answered 403 to a plain script GET; fetch with a normal User-Agent or from a browser.
- Terms as item 11 (BLS.gov content). Use: the best automated check on the committed US calendar fixture.

**13. US BEA Data API** (primary)
- Gives: GDP, personal income and outlays (PCE), trade, regional.
- Access: REST. Register at https://apps.bea.gov/API/signup/ with name or organisation, email and agreement to the ToS; a 36-character UserID is emailed and activated by clicking the link (user guide https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf [read]). Example call form: `https://apps.bea.gov/api/data?&UserID=...&method=GETDATASETLIST&ResultFormat=JSON`. The key travels in the URL; scrub it from logs as the Marketaux client does.
- Limits (user guide [read]): 100 requests per minute, 100 MB per minute, 30 errors per minute; exceeding returns "Request Denied - exceeded Requests per minute quota."
- Terms (https://apps.bea.gov/API/_pdf/bea_api_tos.pdf [read]): "You may use the BEA API to develop a service or service to search, display, analyze, retrieve, view and otherwise 'get' information from BEA data." Notice: "All services, which utilize or access the API, should display the following notice prominently within the application: 'This product uses the Bureau of Economic Analysis (BEA) Data API but is not endorsed or certified by BEA.'" "You may not modify or falsely represent content accessed through the API and still claim the source is the BEA."
- Use: PCE is the Fed's preferred inflation gauge; GDP advance estimates move USD and indices.

**14. BEA release schedule** (primary)
- https://www.bea.gov/news/schedule [read]: iCal `https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics` [live]; JSON `https://apps.bea.gov/API/signup/release_dates.json` [live]; news-release RSS `https://apps.bea.gov/rss/rss.xml`.
- Terms: BEA website policies; the API ToS above applies to the API. Use: machine-readable GDP and PCE dates, the only free official JSON schedule in this list.

**15. Eurostat dissemination API** (primary)
- Gives: HICP flash and final inflation, unemployment, GDP and other euro-area statistics.
- Access: REST, no key. SDMX 2.1 and 3.0, plus JSON-stat 2.0 and SDMX-CSV (getting-started page https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started [read]). Example [live]: `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr?format=JSON&geo=EA&coicop=CP00&lastTimePeriod=1`.
- Cost and limits: free; no numeric limit found [not verified].
- Terms (https://ec.europa.eu/eurostat/en/help/copyright-notice [read]): "Reuse of statistical data, metadata, publications, and other dissemination tools published on this website for commercial or non-commercial purposes is authorised provided the source is acknowledged." Changes to the data must be stated.
- Use: euro-area HICP flash is the key EUR print; the ECB feed will not give the number.

**16. Eurostat release calendar** (primary)
- https://ec.europa.eu/eurostat/news/release-calendar [live]. Machine-readable export [not verified]. Terms as item 15.

**17. ONS release calendar and API** (primary; the repo already has an ONS macro provider)
- Calendar: https://www.ons.gov.uk/releasecalendar offers an RSS link (`releasecalendar?rss&...`), an email-alert link and an add-to-calendar link at `/calendar/releasecalendar` [read]. API: `https://api.beta.ons.gov.uk/v1` [live]; "The API is open and unrestricted - no API keys are required"; it is in Beta, so "there may occasionally be breaking changes" (https://developer.ons.gov.uk/ [read]).
- Limits (https://developer.ons.gov.uk/bots/ [read]): "120 requests per 10 seconds - all site and API assets", "200 requests per 1 minute", "15 requests per 10 seconds - high demand site assets". Respect `Retry-After`; ignoring it can bring "a block ... for up to 1 hour."
- Terms: Open Government Licence v3.0 (registry). Use: UK CPI, labour market and GDP dates for GBP blackouts.

**18. Statistics Canada Web Data Service** (primary)
- https://www.statcan.gc.ca/en/developers/wds [read]: 15 methods; data and metadata "that we release each business day". Key requirement not stated on that page [not verified].
- Terms (Open Licence https://www.statcan.gc.ca/en/terms-conditions/open-licence [read]): "Statistics Canada grants you a worldwide, royalty-free, non-exclusive licence to: use, reproduce, publish, freely distribute, or sell the Information". You must reproduce it accurately and not suggest endorsement.
- Use: Canadian labour force survey and CPI. Useful for CAD pairs; not core.

**19. Australian Bureau of Statistics Data API (beta)** (primary)
- https://www.abs.gov.au/about/data-services/application-programming-interfaces-apis/data-api-user-guide [read]. SDMX 2.1; base `https://data.api.abs.gov.au/rest/data/[query]` (changed 29 November 2024); JSON, XML, CSV; `UpdatedAfter` modifier. Beta; key requirement not stated [not verified].
- Terms (https://www.abs.gov.au/website-privacy-copyright-and-disclaimer [read]): "All material presented on this website is provided under a Creative Commons Attribution 4.0 International licence, with the exception of:" third-party material and similar. Attribute the ABS.
- Use: AUD data; the RBA feed is already recorded. Low priority.

**20. Stats NZ** (primary)
- https://www.stats.govt.nz/large-datasets/csv-files-for-download/ offers CSV files of "the latest data from Infoshare and our information releases". The release-calendar page is JavaScript-rendered and returned no text; API access and licence terms [not verified]. Low priority.

**21. e-Stat (Japan official statistics)** (primary)
- API at https://www.e-stat.go.jp/api/en. "Registration on e-Stat must be required for utilizing API functions"; sign-up at https://www.e-stat.go.jp/en/mypage/user/preregister; an `appId` is required for requests. How to obtain the appId, cost and limits were not on the pages I read [not verified].
- Terms (https://www.e-stat.go.jp/en/terms-of-use [read]): "Information made available on this website (hereinafter referred to as 'Content') may be freely used, copied, publicly transmitted, translated or otherwise modified on condition that the user complies with provisions 1) to 6) below. Commercial use of Content is also permitted." The terms are compatible with CC BY 4.0. API-specific terms are in Japanese at `/api/agreement/`. For content from external databases through API links, "the user must comply with the terms and conditions of the source provider".
- Use: Japanese CPI, labour, trade. Low priority; the BoJ API covers the financial series.

### B3. Data APIs

**22. World Bank Indicators API** (primary)
- `https://api.worldbank.org/v2/country/GB/indicator/FP.CPI.TOTL.ZG?format=json` [live]; no key.
- Terms (https://www.worldbank.org/ext/en/legal/terms-conditions/datasets [read]): "Unless specifically labeled otherwise, these Datasets are provided to you under a Creative Commons Attribution 4.0 International License (CC BY 4.0), with the additional terms below." Attribution form: "The World Bank: Dataset name: Data source (if known)."
- Use: annual and quarterly reference data; too slow for event work. Low priority.

**23. IMF data** (primary)
- Gives: IFS, WEO, balance of payments, primary commodity prices, exchange rate data.
- API endpoints on the new portal (`portal.api.imf.org`) were not read [not verified].
- Terms (https://www.imf.org/en/about/copyright-and-terms, effective 11 October 2024 [read in full]). **Mixed signals; flag.** General: "The IMF allows free non-systematic downloading and/or printing of Content from its Sites by Users for personal, noncommercial usage only without any right to resell, redistribute, compile, or create derivative works." Also: "The IMF prohibits the bulk download of information by automated technology without explicit permission and reserves the right to terminate access to its Sites or Content." And: "The IMF does not permit use of its Content or Sites for the training of large language models (LLMs) without explicit permission." Special terms for published statistical Data: "You may download, extract, copy, create derivative works, publish, distribute, and use Data obtained from IMF Sites, subject to the following conditions", citing the IMF as source. "For any potential commercial reuse of IMF Data, please email copyright@imf.org to request permission."
- Reading: small, targeted API pulls of published Data with attribution look permitted; a scheduled bulk crawl does not. Email copyright@imf.org before automating anything larger than a few series. Because Fiboki runs local LLM agents, keep IMF text out of any fine-tuning set.

**24. OECD Data Explorer API** (primary)
- Permitted use (https://www.oecd.org/en/about/terms-conditions.html, Data section [read]): "you can extract from, download, copy, adapt, print, distribute, share and embed Data for any purpose, even for commercial use. You must give appropriate credit to the OECD ..." Data "may be subject to restrictions beyond the scope of these Terms and Conditions" where third parties own it: check each dataset's source tab. Availability: the OECD "may monitor your use of the Data".
- API explainer (https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html [read]): "rate limiting has been introduced"; the numeric limits were not on the page [not verified]. API base URL [not verified]; use the "Developer API" button in the Data Explorer to get the exact query.
- Use: composite leading indicators, unemployment, CPI for G7. Medium-low.

**25. BIS Data Portal API** (primary; the registry already has BIS speeches)
- Gives: effective exchange rates, policy rates, credit, international banking and derivatives statistics.
- API docs https://stats.bis.org/api-doc/v2/ (SDMX RESTful v2.1.0); bulk files https://data.bis.org/bulkdownload.
- Terms (https://data.bis.org/help/legal [read in full]): "The use of the statistics is unrestricted, provided that:" the BIS is cited as source, translations are labelled unofficial, the use is not misleading, "if the statistics will be used in a commercial publication or product, their inclusion in the publication or product will not result in any additional charge to subscribers or other users", and nothing in them is presented as investment advice. "No other use is permissible." API terms: "The BIS reserves the right to limit or suspend any User's IP address access to the APIs at any time and without notice".
- Use: policy-rate panel and real effective exchange rates for a cross-currency regime feature. Monthly; not an event feed.

**26. DBnomics** (aggregator)
- `https://api.db.nomics.world/v22/` [live]; OpenAPI at `/v22/apidocs`; no key.
- Terms (https://db.nomics.world/about [read]): "Data distributed by DBnomics is subject to the same license and terms of use as its original source provider." The aggregated datasets are under the Open Database License; most fetchers are AGPLv3+.
- Use: one API for many providers (IMF, ECB, national offices), so it inherits each provider's terms, including the IMF caution in item 23. Handy for exploration; for production prefer the primary source.

**27. US Treasury: daily yield curve and FiscalData** (primary)
- Daily Treasury par yield curve: page https://home.treasury.gov/resource-center/data-chart-center/interest-rates/ and XML feed `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM` [live, text/xml]. The interest-rate page carries a "Developer Notice on changes to the XML data feeds", so re-check the URL form before wiring.
- FiscalData REST API (https://fiscaldata.treasury.gov/api-documentation/ [read]): "Our API is open, meaning that it does not require a user account or registration for a token." The page states: "The data is offered free, without restriction, and available to copy, adapt, redistribute, or otherwise use for non-commercial or commercial purposes." (This statement is on the FiscalData site; I did not read a separate statement for the yield-curve pages.)
- Use: 2y, 10y and real yields are the main gold and USD drivers, and the registry's FRED pack already carries DGS2 and DGS10 with a one-day lag; the Treasury page is the primary and publishes the same day.

**28. EIA Open Data API v2** (primary)
- Register at https://www.eia.gov/opendata/register.php; the page says "Registration and compliance with the API Terms of Service Agreement help EIA monitor usage and ensure service availability." Terms: https://www.eia.gov/opendata/terms-of-service.php (not read). Rate limits not on the pages I read [not verified].
- Copyright (https://www.eia.gov/about/copyrights_reuse.php [read]): "U.S. government publications are in the public domain and are not subject to copyright protection. You may use and/or distribute any of our data, files, databases, reports, graphs, charts, and other information products ..." with an acknowledgement requested.
- Use: crude oil and gas prices for CAD, NOK and energy-heavy indices. The registry's FRED pack already holds WTI (DCOILWTICO).

**29. SEC EDGAR APIs** (primary)
- `https://data.sec.gov/submissions/CIK##########.json` [live] and XBRL company facts; "These APIs do not require any authentication or API keys to access." (https://www.sec.gov/search-filings/edgar-application-programming-interfaces [read]).
- Fair access (https://www.sec.gov/os/accessing-edgar-data [read]): "Current max request rate: 10 requests/second"; declare a User-Agent with a company name and contact email; "Download only what you need"; the SEC "does not allow botnets or automated tools to crawl the site".
- **Full-text search** (`sec.gov/edgar/search/`): the API page documents no full-text search API, only a link. Do not automate it. Use the submissions API per CIK for 8-K timing of index heavyweights.
- Use: index (US500, NAS100) heavyweight filings and earnings. Medium-low for FX and gold.

### B4. Vendor APIs (news and market data)

**30. Alpha Vantage** (aggregator)
- Key: https://www.alphavantage.co/support/#api-key (form: role, organisation, email; "lifetime access") [read].
- Free tier: FAQ [read]: "free stock API service covering the majority of our datasets for 25 API requests per day".
- **News is premium.** `NEWS_SENTIMENT` ("Market News & Sentiment") says in the documentation: "Tip: this is a premium API function." (https://www.alphavantage.co/documentation/ [read]). Other pages tag many free endpoints "Premium" too, but the tip on this endpoint is explicit. `FX_DAILY` is free.
- Terms (ToS PDF https://www.alphavantage.co/terms_of_service/ [read]): licence "for personal, non-commercial use"; commercial use includes any purpose that "goes beyond investment analysis, research, testing, monitoring, and any other activities that are private and individual in nature".
- Verdict: no free news. Daily FX is available but you already have OANDA. Skip.

**31. NewsAPI.org** (aggregator)
- Developer plan (https://newsapi.org/pricing [read]): free, "100 requests per day", "Articles have a 24 hour delay", "No uptime SLA". Business plan "$449 per month".
- **Terms forbid your use** (https://newsapi.org/terms [read]): "The Developer plan may be used for development and testing in a development environment only, and cannot be used in a staging or production environment (including internally)." Integrated outside development, "license to use the Developer plan will be revoked".
- Verdict: unusable for a running recorder, and 24-hour delay defeats the purpose. Do not wire.

**32. Massive (Polygon)** (aggregator and exchange data)
- Forex "Currencies Basic": $0, "5 API Calls / Minute", "2 Years Historical Data", "End of Day Data", "Minute Aggregates", labelled "Individual use" (https://massive.com/currencies pricing tab [read]). Starter is $49 per month.
- Terms (https://massive.com/legal/individuals-terms-of-service [read in part]): "solely for your own personal, non-commercial, and non-business purposes". Storage and caching clauses were not located in the part I read [not verified].
- News: the stocks REST API has a News endpoint (https://massive.com/docs/rest/stocks/news); whether the free plan includes it was not shown [not verified]. Benzinga news is a partner dataset at "$99/month per dataset".
- Verdict: a second FX price source at 5 calls per minute; not a news source for free.

**33. Twelve Data** (aggregator)
- Basic: free, "8 API (800 a day)" (https://twelvedata.com/pricing [read]). Prices and reference data; no news evidence found.
- Terms (https://twelvedata.com/terms [read]): the customer shall not "Store or cache Data beyond permitted timeframes specified in the Documentation" and shall not "Use data for high-frequency trading without appropriate license". Redistribution barred except via a Redistribution Rights Add-On.
- Verdict: storage limits make it a poor fit for a point-in-time research archive. Skip.

**34. EODHD** (aggregator)
- Free: "20" API calls per day, 20 requests per minute; calls cost "1 for EOD/live, 5 for intraday & news" (https://eodhd.com/pricing [read]), so about four news calls a day.
- Terms (https://eodhd.com/financial-apis/terms-conditions [read]): "Non-Professional Users are permitted to store, manipulate, and analyze the data for private, non-commercial purposes", but not to resell, retransmit, redistribute or display.
- Verdict: storage is permitted for personal use, but 20 calls a day is too few. Skip.

**35. Financial Modeling Prep** (aggregator)
- Basic (free): "250 Calls / Day", end-of-day historical data, profile and reference data. "Financial Market News" and "Crypto and Forex" appear from the Starter plan, $19 per month billed annually (https://site.financialmodelingprep.com/developer/docs/pricing [read]).
- Terms page returned 403 to my fetch [not verified].
- Verdict: news is paid. Skip.

**36. Tiingo** (aggregator)
- Starter (free): 50 requests per hour, 1000 per day, 500 unique symbols per month, 1 GB per month; licence "Internal Use Only" (https://www.tiingo.com/about/pricing [read, screenshot]).
- **Tiingo News is not in the free plan** (cross on Starter, tick on the $30 per month Power plan; "News API allows 3 Months of queryable history").
- Verdict: news is paid. Skip.

### B5. Automated access forbidden

**37. CME Group website data** (primary exchange, but forbidden to script)
- A scripted GET to `cmegroup.com` returns HTTP 403 with this text: "This IP address is blocked due to suspected web scraping activity associated with it on this CMEgroup.com page. Use of scripts, software, spiders, robots, avatars, agents, tools or other scraping mechanisms is strictly prohibited by CME Group's website Data Terms of Use. If you are attempting to access data or content from the website via automated means or for commercial purposes, CME has numerous other methods to deliver the content you require."
- Verdict: **do not script CME pages.** CME's licensed route is a market data licence (not free). Futures settlement data is available from licensed vendors; the CFTC COT already in the registry covers positioning.

### Not assessed

- **Trading Economics**: the API is a paid product; I could not retrieve its terms or any free-tier statement [not verified]. Not recommended until terms are read.
- **Marketaux, GDELT, CFTC COT, ForexFactory**: in the registry already.

---

## C. Flags: automated access or storage forbidden or doubtful

| Source | Issue | Evidence |
|---|---|---|
| CME Group site | Scripts, robots, scraping prohibited | Block page quoted in item 37 |
| NewsAPI.org free plan | Not for staging, production "(including internally)" | Item 31 |
| IMF | "prohibits the bulk download of information by automated technology without explicit permission"; no LLM training | Item 23 |
| Twelve Data | "Store or cache Data beyond permitted timeframes" | Item 33 |
| Finnhub | Terms page disallowed to robots; free plan "Personal Use"; storage right unconfirmed | A1 |
| Alpha Vantage | Personal, non-commercial only; news premium | Item 30 |
| FRED | Third-party "Copyright" series need owner's permission beyond personal use; mandatory notice | A2 |
| BoJ | Content is personal-only (registry); API requires courtesy notice and credit if released | Item 5 |
| SEC EDGAR full-text search | No documented API; "does not allow botnets or automated tools to crawl the site" | Item 29 |
| Bank of Canada Valet | Circumventing request-frequency limits is prohibited (limits unpublished) | Item 1 |
| DBnomics | Inherits the source's terms | Item 26 |

None of the sources in B1 to B3 forbids a low-frequency, identified, personal-research poll. Use a descriptive User-Agent that names the tool and a contact address (SEC and ONS ask for it explicitly), keep to the stated limits, and honour `Retry-After`.

---

## D. Ranked shortlist: the 10 to wire first

Premise: the platform already records 13 central-bank feeds plus BIS speeches, ALFRED, ECB SDMX, BoE IADB, ONS, NY Fed and CFTC. The scarce thing is a **machine-checkable official calendar** (the blackout calendar is a fixture refreshed by hand) and **first-print values for the high-impact USD, EUR and GBP releases**, followed by the central banks with no feed. No free news API beyond Finnhub and GDELT survives the terms test (items 30 to 36), so none of them ranks.

| Rank | Source | Why | Effort and gate |
|---|---|---|---|
| 1 | **BLS schedule ICS + BLS API v2** (items 11, 12) | NFP and CPI are the largest USD and gold events; the ICS can verify the fixture automatically; the API gives the print. | Free key by email with CAPTCHA (human step). 500 queries a day is ample. |
| 2 | **BEA schedule JSON/ICS + BEA API** (items 13, 14) | GDP and PCE; the only official JSON schedule. | Free key by email. Show the BEA notice if any UI displays the data. |
| 3 | **Treasury yield curve XML + Fed H.10/H.15 feeds** (items 6, 27) | Real yields and the dollar drive gold and USD pairs; primary and same day (the FRED pack lags a day). | No key. Check the Treasury "Developer Notice" on feed URLs. |
| 4 | **Eurostat API + release calendar** (items 15, 16) | HICP flash is the key EUR print; no key; commercial reuse allowed with source. | No key. Add the release calendar once a machine format is confirmed. |
| 5 | **ONS release calendar RSS** (item 17) | GBP event dates from the publisher; the ONS provider already exists. | No key. Respect ONS rate limits. |
| 6 | **Bank of Canada RSS + Valet** (item 1) | Closes the CAD central-bank gap; clean policy-rate and FX series. | No key. Stored as `OTHER` unless the enum migrates. |
| 7 | **RBNZ, Riksbank (and Norges Bank when an RSS URL is confirmed)** (items 2, 3, 4) | Closes NZD and SEK gaps; RBNZ news feed and Riksbank speeches [live]. | No key. Read RBNZ terms first; Norges Bank needs a confirmed feed URL. |
| 8 | **FOMC, ECB, BoE and BoJ calendar pages** (items 7 to 10) | Cross-checks for the hand-kept central-bank dates; weekly scrape of one page each. | No key. HTML parsing, so keep it as a diff report like `calendar_diff`, never a blackout source. |
| 9 | **BIS Data Portal API** (item 25) | Unrestricted-use statistics with citation; policy rates and effective exchange rates in one place. | No key. Monthly cadence. |
| 10 | **EIA API** (item 28) | Oil for CAD, NOK and energy indices; public domain. | Free key. Overlaps the FRED pack's WTI, so low urgency. |

Next best: BoJ Time-Series API (item 5), SEC EDGAR submissions for index heavyweights (item 29), Statistics Canada WDS (item 18), DBnomics for exploration (item 26). Hold IMF (item 23) until copyright@imf.org answers.

The two keys in section A (FRED, Finnhub) remain first: they unlock code that is already built.

If you wire items 11 to 13 or 28 you will want new variable names. **Proposed, not read by any code today**: `FIBOKI_BLS_API_KEY`, `FIBOKI_BEA_API_KEY`, `FIBOKI_EIA_API_KEY`. `api/settings.py` keeps an `ENV_REGISTRY` and warns about unknown `FIBOKI_` names, so add them there when the providers are written.

---

## E. Gaps in this research

- Finnhub's terms of service were not read (robots.txt). Read them yourself before relying on storage.
- RBNZ copyright, Riksbank content terms, Stats NZ licence and API, EIA terms, OECD numeric rate limits, FMP terms, Trading Economics and Eurostat limits were not retrieved.
- I did not measure latency for any source, and I did not test any endpoint with a key.
- Several sites block scripted fetches (BLS pages, CME, SEC, IMF, OECD, FMP, RBNZ index); I read those in a browser. Your own scripts may meet the same 403, so set a descriptive User-Agent.
- Pricing and terms change; re-read the terms of any source on the day you switch it on, and record the date, as the registry's `terms_summary` does.
