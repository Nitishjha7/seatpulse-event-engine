# SeatPulse — Interview Prep

Har sawaal ka format: **sawaal → wo kyu pooch raha hai → jawab (asli numbers ke saath)**.

> **Ek usool:** jawab 30-60 second ka hona chahiye. Lamba jawab confidence nahi, ghabrahat dikhata hai. Chhota jawab do, phir ruk jao — interviewer khud khodega jahan usse interest hai.

**Is project ke asli numbers (yaad rakho, ye tumhari sabse badi taakat hain):**

| Kya | Number |
|---|---|
| Flash sale test | 200 concurrent users, ek seat |
| Total requests | 8,154 · **0 failures** · 137 req/s |
| Result | DB me **exactly 1** confirmed booking |
| Latency (flash sale) | p50 1,000 ms · p99 1,400 ms |
| Latency (50 users, normal) | p50 13-21 ms · p95 85-95 ms |
| Tests | 29 (auth + RBAC + rate limit + concurrency) |
| Load test se mile bugs | 3 (sab fix, sab measured) |
| Test se mile bugs | 2 (rate-limit peek, SQLAlchemy cascade) |

---

## 1. Opening — pehle 60 second

Ye sabse important hai. Yahi tay karta hai ki aage kaunse sawaal aayenge.

> "SeatPulse ek event ticketing platform hai — BookMyShow jaisa. Asli problem jo maine solve ki: **flash sale me overselling**. Jab 5000 log ek saath ek hi seat pe click karte hain, naive `SELECT → check → UPDATE` wala flow wo seat kai baar bech deta hai.
>
> Maine teen layers banayi — Redis distributed lock speed ke liye, Postgres optimistic locking correctness ke liye, aur ek partial unique index aakhri guarantee ke liye.
>
> Aur maine sirf banaya nahi, **prove kiya**: Locust se 200 concurrent users ek hi seat pe, 8000+ requests, zero failures, aur database me exactly ek booking. Us load test ne teen asli race conditions pakdi jo maine `pg_stat_activity` se debug karke fix ki."

**Is pitch me jaan-boojh ke teen hook chhode hain** — teen layers, load test numbers, aur "teen bugs". Interviewer inme se kisi ek pe khodega, aur tum tayyar ho.

---

## 2. Tech choices — "ye kyu, wo kyu nahi"

> **Ye sawaal trap nahi hai.** Wo check kar raha hai ki tumne **soch ke chuna** ya tutorial copy kiya. Jawab ka shape hamesha:
> *"Ye constraint tha → isliye ye chuna → constraint badalta to X leta."*

### FastAPI kyu?

> "Do cheezein chahiye thi — WebSockets aur high concurrency. FastAPI ASGI-native hai to WebSocket first-class hai; Django me Channels alag se lagana padta. Pydantic se validation aur OpenAPI docs free mile. Flask lete to async aur WebSocket dono bolt-on hote."

### React kyu? Next.js kyu nahi?

> "Seat grid me 100 cells hain jinka state independently badalta hai — WebSocket message aane pe sirf ek seat re-render honi chahiye, poora grid nahi. React ka reconciliation exactly yahi karta hai.
>
> Next.js nahi liya kyunki ye poora authenticated dashboard hai — SSR ya SEO ka koi faayda nahi tha. Wo ek build layer extra add karta bina kuch diye."

### PostgreSQL kyu, MongoDB nahi?

**Ye sabse achha jawab hai — yahan poori taakat lagao.**

> "Ye is project ki sabse important choice thi. Mera overselling ka aakhri bachav ek **partial unique index** hai — ek seat, ek confirmed booking, database level pe. Aur seat update + booking insert ek hi transaction me hone chahiye.
>
> Mongo me multi-document transactions hain, par wo uski strength nahi hai. Mujhe yahan **ACID chahiye tha, flexible schema nahi**. Seat ka schema kabhi badalta hi nahi — wo hamesha row, number, status, price rahega."

### Redis kyu? Sirf database se kaam nahi chalta?

> "**Chalta hai** — aur chal raha tha. Phase 3 me DB-only version tha aur wo 20 concurrent requests pe bilkul sahi kaam kar raha tha.
>
> Redis correctness ke liye nahi hai, **load ke liye** hai. 5000 me se 4999 requests Redis pe hi ruk jaati hain, database tak pahunchti hi nahi. Aur TTL se abandoned cart ka cleanup free me mil gaya — koi cron job nahi likhna pada."

> ⭐ Ye jawab isliye strong hai kyunki tum Redis ko **glorify nahi kar rahe**. Zyadatar candidates bolte hain "Redis se overselling rukti hai" — wo galat hai.

### WebSocket kyu, polling kyu nahi?

> "1000 clients har 2 second poll karein to 500 req/s sirf 'kuch badla kya?' poochne me nikal jaate. WebSocket me traffic tabhi hota hai jab actually kuch badle.
>
> SSE bhi chal jata — flow one-directional hai. WebSocket isliye liya ki aage kuch two-way karna ho to base ready ho."

### Go ya Java behtar nahi hota?

**Yahan defensive mat hona. Maan lo, phir asli baat pe le aao.**

> "Haan, is workload pe Go behtar hota — goroutines me per-request cost bahut kam hai, mujhe threadpool aur pool sizing ka poora jhamela hi na hota.
>
> Python isliye liya ki ecosystem — SQLAlchemy, Alembic, Pydantic — mujhe business logic pe focus karne deta hai.
>
> Par asli baat ye hai: **bottleneck runtime nahi tha, Postgres ki row-level contention thi.** Ek hi seat pe 200 log — wo row Go me bhi utni hi serialized rahegi. Language badal ke wo problem hal nahi hoti."

---

## 3. Core — concurrency aur locking

### Overselling kaise rokte ho? (sabse common sawaal)

> "Teen layers, upar wali sabse tez aur neeche wali sabse pakki:
>
> **1. Redis lock** — `SET seat:42:lock <user> NX EX 300`. Ek atomic command. 5000 requests me se theek ek ko `True` milta hai.
>
> **2. Optimistic locking** — seat pe ek `version` column hai. Update aisa chalta hai: `UPDATE seats SET status='booked', version=version+1 WHERE id=? AND version=?`. Do parallel updates me ek ka `WHERE` match nahi karega, use `rowcount 0` milega, aur mai 409 return karta hoon.
>
> **3. Partial unique index** — `UNIQUE(seat_id) WHERE status='confirmed'`. Ye database ka apna niyam hai. Redis down ho, mere code me bug ho, do server chal rahe hon — Postgres dusri confirmed booking insert hone hi nahi dega."

### Teeno layers ki zaroorat kya hai? Ek se kaam nahi chalta?

**Ye sabse achha follow-up hai. Har layer ke bina kya tootta hai, wo batao:**

| Sirf ye layer | Kya tootega |
|---|---|
| Sirf Redis | Redis restart hote hi saare locks gayab. Us window me overselling ho sakti hai. Redis me durability hai hi nahi — aur maine jaan-boojh ke usme volume nahi diya |
| Sirf version column | Correct hai, par **har** request DB tak jaati hai. 5000 requests = 5000 DB round trips |
| Sirf unique index | Correct hai, par har failure ek `IntegrityError` ban jayega. Exception se flow control karna mehenga aur ganda hai |

> "To Redis speed deta hai, version column zyadatar clash pakad leta hai, aur index aakhri insurance hai jo umeed hai kabhi trigger na ho."

### Optimistic vs Pessimistic — tumne optimistic kyu chuna?

> "Pessimistic matlab `SELECT ... FOR UPDATE` — row ko lock karke baaki sabko wait karwana. Optimistic matlab lock nahi lena, bas ye maan ke chalna ki clash kam hoga, aur clash ho jaye to **detect** kar lena.
>
> Maine optimistic isliye chuna kyunki mere paas **upar Redis already hai**. Redis 99% requests pehle hi reject kar deta hai, to DB tak jo pahunchti hai unme clash bahut kam hota hai — aur wahi case optimistic ke liye best hai.
>
> Pessimistic me har request row lock ke liye queue me lagti, chahe clash ho ya na ho. Aur agar Redis na hota, to shayad pessimistic behtar hota."

**Follow-up "prove karo?":**
> "Wo mere roadmap me hai — pessimistic variant likh ke usi Locust suite se dono ka p99 aur throughput compare karna. Abhi maine wo maapa nahi hai, isliye claim nahi karunga."

> ⭐ "Maine maapa nahi to claim nahi karunga" — ye line tumhari credibility badha deti hai, ghatati nahi.

### `rowcount == 0` kaise pata chalta hai ki race hui?

> "`UPDATE ... WHERE id=? AND version=3` — agar koi aur pehle jeet gaya to version 4 ho chuka hoga aur mera `WHERE` kisi row se match nahi karega. Postgres `rowcount 0` deta hai. Wo mera signal hai ki mera data purana tha, aur mai 409 return kar deta hoon.
>
> Sabse zaroori baat: ye **ek atomic statement** hai. Read aur write alag steps nahi hain — isliye beech me koi ghus hi nahi sakta."

### Booking se pehle jo `if seat.status != 'available'` check hai, wo kaafi kyu nahi?

**Ye tez interviewer ka sawaal hai. Iska jawab tumhe pata hona chahiye:**

> "Wo check bilkul kaafi nahi hai, aur wo maine sirf **achha error message** dene ke liye rakha hai.
>
> Us line aur neeche wale UPDATE ke beech microseconds ka gap hai. Us gap me dusra request wahi seat le sakta hai. Asli guarantee UPDATE ke `WHERE` clause me hai — kyunki database ek row pe do UPDATE ek saath nahi chalne deta."

### Isolation level kaunsa use kiya?

> "Postgres ka default — **READ COMMITTED**. Maine badla nahi.
>
> Wajah: mai isolation level pe depend hi nahi kar raha. Mera guarantee ek atomic conditional UPDATE se aata hai aur ek unique index se. Ye dono READ COMMITTED me bhi utne hi pakke hain.
>
> SERIALIZABLE pe jata to serialization failures pe retry logic likhna padta, aur throughput girta — bina kuch extra safety mile."

---

## 4. Redis — detail me

### `SET NX EX` — ye ek command kyu, do kyu nahi?

> "`NX` matlab 'sirf tab set karo jab key exist na kare'. Ye Redis ke andar **atomic** hai.
>
> Agar mai `EXISTS` check karke phir `SET` karta, to un do commands ke beech doosra client wahi key set kar sakta tha. Ek command me wo gap hai hi nahi. Redis single-threaded hai — commands ek-ek karke chalte hain.
>
> `EX 300` matlab 5 min ka TTL. Ye sabse elegant hissa hai: user cart chhod ke chala gaya, laptop band ho gaya, tab crash ho gaya — **seat apne aap free ho jayegi**. Mujhe koi cleanup job nahi likhna pada."

### Lock release me Lua script kyu? Seedha `DEL` kyu nahi?

> "Kyunki seedha `DEL` **kisi aur ka lock** delete kar sakta hai:
>
> 1. User A ka lock hai, wo 5 min me expire ho gaya
> 2. User B ne turant lock le liya
> 3. User A ka 'release' request ab aata hai aur `DEL` kar deta hai
> 4. B ka lock ud gaya — jabki B ne kuch galat nahi kiya
>
> To pehle check karna padta hai 'lock mera hi hai?', tabhi delete. Par Python me `GET` phir `DEL` likhta to unke beech bhi wahi race reh jati. Lua script Redis ke andar atomic chalti hai — check aur delete ek saath."

**Test bhi hai iska:** `test_cannot_release_someone_elses_lock` — response me `released: false` aata hai aur asli lock salamat rehta hai.

### Redis mar jaye to?

> "Do alag sawaal hain isme.
>
> **Correctness** — bilkul safe. Postgres ke version column aur unique index tab bhi kaam karte hain. Overselling phir bhi nahi hogi.
>
> **Availability** — locks chale jaayenge, matlab jo seats hold thi wo turant available dikhne lagengi, aur load DB pe aa jayega. Degrade hoga, tootega nahi.
>
> Aur maine Redis me **jaan-boojh ke volume nahi diya** — usme sirf 5-minute ke temporary locks hain. Paisa aur booking hamesha Postgres me hai. Redis kabhi source of truth nahi hai."

### Redis me persistence kyu nahi rakhi?

> "Design decision hai, laparwahi nahi. Usme sirf temporary locks hain jo waise bhi 5 min me mar jaate hain. Restart pe wo chale bhi jaayein to nuksan kya — seats available ho jaayengi, jo already correct state hai. Persistence rakhta to disk I/O ka kharcha uthata bina kisi faayde ke."

### TTL 5 minute kyu? 1 minute ya 30 minute kyu nahi?

> "Trade-off hai. Chhota rakho to user payment ke beech me seat kho deta hai. Bada rakho to abandoned carts seats ghere baithe rehte hain aur inventory block ho jaati hai.
>
> 5 minute checkout ka realistic time hai. Aur ye config me hai (`SEAT_LOCK_TTL`), hardcoded nahi — kyunki asli number production ke data se aata hai, meri guess se nahi."

---

## 5. WebSockets

### Real-time update kaise kaam karta hai?

> "Har event ka apna Redis pub/sub channel hai — `seatpulse:event:1`. Jab bhi koi seat lock, release, book ya cancel hoti hai, backend us channel pe publish karta hai. Har worker usi channel ko subscribe kiye baitha hai aur apne connected sockets ko forward kar deta hai."

### Redis pub/sub kyu? Seedha sockets pe broadcast kyu nahi?

**Ye system design ka asli sawaal hai:**

> "Ek server ho to seedha broadcast kaafi hai. Par production me 2-3 uvicorn workers chalte hain, aur **har worker ke paas apne alag sockets hote hain**.
>
> Maan lo User A ka lock Worker 1 pe process hua, aur User B ka socket Worker 2 pe hai. Worker 1 sirf apne local sockets ko batayega to User B ko kabhi pata hi nahi chalega.
>
> Redis message bus ban jata hai — har worker publish bhi karta hai aur subscribe bhi. Aur Redis pehle se stack me tha, koi nayi service nahi lagi."

### Message drop ho jaye to?

> "Redis pub/sub **at-most-once** hai — koi persistence nahi, koi replay nahi. Message drop ho sakta hai.
>
> Isliye maine WebSocket ko **optimization** rakha hai, source of truth nahi. Reconnect hone par frontend poori seat list dobara fetch karta hai. Aur booking khud kabhi WebSocket pe depend nahi karti — wo hamesha HTTP request se hoti hai jisme teeno safety layers hain.
>
> Agar mujhe guaranteed delivery chahiye hoti — jaise payment events — tab Kafka ya Redis Streams lagta."

### Broadcast fail ho jaye to booking ka kya?

> "Booking ho jayegi. Mera `publish()` exception swallow karta hai aur sirf warning log karta hai. Real-time update **nice-to-have** hai, booking **must-have** hai. Ek notification fail hone se paisa lene wala flow nahi tootna chahiye."

### Connection toot jaye to?

> "Frontend hook me **exponential backoff** hai — 1s, 2s, 4s, 8s, max 15s. Fixed 1-second retry rakhta to server down hone par 100 clients har second hammer karte aur wo uthne hi na paata. Successful connect pe counter reset ho jata hai.
>
> Reconnect ke baad poora state dobara fetch hota hai, taki disconnect ke dauraan chhoote hue messages ki bharpai ho jaye."

### WebSocket authenticate kaise kiya?

> "Access token query param me — `?token=...`. Header se nahi, kyunki **browser ka WebSocket API custom headers bhejne hi nahi deta**.
>
> Trade-off ye hai ki URL server logs me aa sakta hai. Isliye wahan sirf short-lived access token bhejta hoon, 30 minute wala — refresh token kabhi nahi. Token galat ho to close code 1008 ke saath connection band."

---

## 6. Auth

### Token kahan store karte ho? (ye zaroor poocha jayega)

> "Access token React ki **memory me** — localStorage me bilkul nahi. Refresh token **httpOnly cookie** me.
>
> localStorage ko koi bhi JavaScript padh sakta hai — koi XSS, koi npm package, koi browser extension. httpOnly cookie JS se readable hi nahi hoti.
>
> Par sab kuch cookie se bhi nahi karta, kyunki cookie har request me apne aap jaati hai — wo CSRF ka darwaza kholta hai. Isliye **asli kaam Authorization header karta hai** (jo CSRF me automatically nahi jata), aur cookie sirf naya access token lene ke liye use hoti hai, `path=/api/auth` aur `SameSite=Lax` ke saath."

### Reload pe access token chala jata hai, phir?

> "Wahi to chahiye. App mount hote hi ek `/refresh` maarti hai — cookie valid hui to session turant wapas, user ko pata bhi nahi chalta.
>
> Yahi mechanism Google login me bhi kaam aata hai: backend cookie set karke frontend pe redirect kar deta hai, aur mount-refresh session bana deta hai. **Token kabhi URL me nahi jata.**"

### JWT stateless hai — logout kaise kaam karta hai?

**Ye tez sawaal hai. Zyadatar candidates yahan atak jaate hain.**

> "Sahi pakda — plain JWT me logout ka koi matlab hi nahi hota, token expiry tak zinda rehta hai.
>
> Isliye har refresh token me ek `jti` hai jo **Redis me whitelist** hoti hai, token ki expiry ke barabar TTL ke saath. Logout us key ko delete kar deta hai — token turant bekaar, bhale uski JWT expiry 7 din baaki ho.
>
> Access token phir bhi 30 minute tak technically valid rahega. Isiliye maine use short rakha hai. Har request pe DB check karta to JWT ka stateless faayda hi khatam ho jata."

### Refresh token rotation kya hai?

> "Har `/refresh` call pe purana token revoke hota hai aur naya milta hai.
>
> Faayda: token chori ho jaye aur attacker use kare, to asli user ka token invalid ho jayega aur uska logout ho jayega. **Chori pakdi jaati hai** — chupchap chalti nahi rehti."

### bcrypt kyu, SHA256 kyu nahi?

> "Kyunki bcrypt **jaan-boojh ke dheema** hai — ~100ms. SHA256 fast hai, aur passwords ke liye fast hona hi problem hai: attacker ek second me crores guesses kar leta. bcrypt pe wahi attack practically namumkin ho jata hai. Salt bhi apne aap andar aa jata hai.
>
> Aur ye slowness ne mujhe load test me kaata bhi — us kahani pe aage aata hoon."

### Google OAuth me Authorization Code flow kyu?

> "Do wajah:
>
> Purana **Implicit flow** token seedha URL me deta tha — wo browser history aur server logs me chhap jata.
>
> Aur **frontend-only OAuth** me `client_secret` browser me chala jata, jahan koi bhi use padh sakta hai. Authorization Code flow me code ka exchange **server-to-server** hota hai — secret kabhi browser tak pahunchta hi nahi.
>
> `state` parameter bhi hai CSRF ke liye — random string Redis me rakhta hoon, Google wahi wapas bhejta hai, match na kare to reject."

### Google user ka email badal gaya to?

> "Isiliye maine match **`google_id` (sub claim) pe** kiya hai, email pe nahi. Email badal sakta hai, `sub` kabhi nahi badalta.
>
> Aur agar us email se password wala account pehle se hai, to mai use **link** kar deta hoon — duplicate account nahi banata."

---

## 7. ⭐ Load testing aur teen bugs — yahan sabse zyada number hain

> Ye tumhara sabse strong section hai. Zyadatar candidates ke paas load test hai hi nahi, aur jinke paas hai unhone usse **bug nahi pakda**.

### Test kaise kiya?

> "Locust se do scenarios. Ek flash sale — 200 concurrent users, sab ek hi seat pe. Dusra realistic browsing — 50 users grid dekh rahe hain aur kabhi-kabhi book kar rahe hain.
>
> Par Locust ka 'zero failures' mere liye kaafi nahi tha. Wo dono requests ko 201 de sakta hai aur dono ko success gin sakta hai. Isliye maine ek `verify_integrity.py` likha jo test ke baad **database** se poochta hai — kya kisi seat ki do confirmed bookings hain, kya seat status aur bookings match karte hain.
>
> Result: 8,154 requests, zero failures, aur database me exactly ek booking."

### Load test se koi bug mila?

**Enthusiasm se bolo. Bug milna achhi baat hai.**

> "Teen mile, aur teeno alag-alag kism ke the."

#### Bug 1 — Lost update race

> "`lock_seat` ka DB update bina status guard ke tha. Sequence ye tha:
>
> B ne seat padhi (A ke lock me thi), check pass ho gaya. A ne beech me book kar li — status `booked`, Redis lock release. B ko ab wo free Redis lock mil gaya, aur usne DB me `status = locked` likh diya — **`booked` ko overwrite kar diya**.
>
> Nateeja: ek confirmed booking thi, par seat booked dikhti hi nahi thi.
>
> **20-request test me ye kabhi nahi pakda gaya.** 500 users pe hi wo timing window khuli. Fix wahi guarded-update pattern se — `WHERE status IN ('available','locked')`, aur `rowcount 0` aaye to Redis lock wapas chhod ke 409."

#### Bug 2 — bcrypt transaction khuli rakhta tha

> "Auth add karne ke baad load test phat gaya — 58 failures, p99 21 second, `QueuePool limit reached`.
>
> Guess karne ke bajaye maine Postgres se poocha:
>
> ```sql
> SELECT count(*) FILTER (WHERE state='idle in transaction'), count(*) FILTER (WHERE state='active')
> FROM pg_stat_activity WHERE datname='seatpulse';
> ```
>
> Jawab aaya: **50 me se 50 connections 'idle in transaction', sirf 1 active.** Matlab kaam koi nahi kar raha tha, sab connections pakde baithe the.
>
> Wajah: SQLAlchemy pehli query pe transaction khol deta hai aur commit tak khuli rehti hai. Login me user read karta hoon, phir 100ms bcrypt chalta hai — utni der connection block. Fix: read ke turant baad `db.commit()`, bcrypt se pehle."

#### Bug 3 — In-flight requests > connection pool

**Ye sabse achha wala hai — poora sunao.**

> "Pool badha ke bhi problem gayi nahi. Root cause ye tha:
>
> Mere routes sync hain, aur `get_db` request ke **shuru me** connection pakad leta hai. Phir request threadpool slot ka wait karti hai — aur us poore intezaar me connection pakda hi rehta hai. Isliye held connections threadpool size se bhi zyada ho gaye.
>
> Aur pool badhana koi fix nahi hai — in-flight requests **unbounded** hain. Kitna bhi pool rakho, load badhne pe phir phategi.
>
> To maine **admission control** lagaya — ek semaphore middleware jo pool se kam requests andar aane deta hai. Ab request connection pakadne se pehle darwaze pe rukti hai.
>
> Invariant साफ hai: `MAX_CONCURRENT_REQUESTS (30) < pool_size + max_overflow (40)`.
>
> Result: **1,250 requests aur 58 failures se 8,154 requests aur zero failures.** Throughput 30 se 137 rps, p99 21 second se 1.4 second."

**Follow-up "p50 to badh gaya (470ms → 1000ms)?":**
> "Haan, kyunki ab requests queue me lagti hain. Par pehle wala 470ms **jhootha** tha — usme 58 requests fail ho rahi thi aur p99 21 second tha. Ab har request poori hoti hai. **Slow response 500 error se hazaar guna behtar hai.**"

### Ye teen bug ek line me kya sikhate hain?

> "Ki correctness ko **maapna** padta hai, maan nahi sakte. Teeno bug ka code padh ke pata nahi chal sakta tha — teeno load ke neeche hi dikhe."

---

## 8. Scaling — "10x traffic aaye to?"

### Ab scale kaise karoge?

> "Order me:
>
> **1. Multiple workers** — abhi ek uvicorn worker hai. `--workers 4` pe jaunga. Mera design pehle se ready hai: Redis lock cross-worker kaam karta hai, aur WebSocket broadcast Redis pub/sub se hota hai — ye dono maine isiliye aise banaye.
>
> **2. Async database driver** — sync SQLAlchemy meri sabse badi limitation hai. `asyncpg` pe jaunga, tab ek worker kahin zyada concurrency handle karega.
>
> **3. Read replicas** — seat grid padhna sabse zyada hone wala operation hai. Wo replica pe ja sakta hai, writes primary pe.
>
> **4. Queue** — flash sale me sabko turant jawab dene ke bajaye ek waiting room, jaisa Ticketmaster karta hai."

### Ek seat pe 50,000 log aa jaayein to?

> "Ye alag problem hai. Us case me lock lena hi bekaar hai — 49,999 log ko 409 milega aur experience ghatiya hoga.
>
> Asli hal **waiting room** hai: users ko ek queue me daalo, aur unhe batches me booking window do. Ye Redis sorted set se ho sakta hai. Ye mere roadmap ke aage ka kaam hai, abhi banaya nahi hai."

### Do datacenter me chalana ho to?

> "Tab Redis lock kaafi nahi hai — cross-region Redis me latency aur split-brain ka issue aata hai. Wahan seat inventory ko region-wise **shard** karna padta, ya Redlock jaisa algorithm lagta.
>
> Par imaandari se — ye is project ka scale nahi hai. Ek region me ek Redis bilkul theek hai."

---

## 9. Kamzoriyan — khud bata do

> ⭐ Ye counter-intuitive lagta hai par **sabse strong move** hai. Jo apni limitation khud jaanta hai, wo senior lagta hai.

### "Kya dobara alag karte?"

> "Teen cheezein:
>
> **1. Sync ki jagah async.** Maine sync routes aur sync SQLAlchemy use kiya. Isi wajah se mujhe admission control lagana pada. `asyncpg` + async SQLAlchemy hota to ek worker kahin zyada handle karta.
>
> **2. Payment ke saath booking ka consistency.** Abhi booking me payment hai hi nahi — aur wo sirf ek missing feature nahi, ek missing *problem* hai. Detail agle section me.
>
> **3. Seat model.** Abhi har seat ek row hai. General admission events ke liye — jahan sirf count matter karta hai, seat number nahi — ye faltu hai. Wahan ek counter chahiye, 5000 rows nahi."

### "Kya nahi kiya jo karna chahiye tha?"

> "**Payments.** Booking payment ke bina aadhi hai — aur wo sirf ek missing feature nahi, ek missing *problem* hai. Jaise hi paisa aata hai, poora consistency ka sawaal khulta hai jo abhi mere project me nahi hai.
>
> Wo mera agla phase hai, aur mujhe pata hai usme kya karna padega — agle sawaal me bata deta hoon."

Aur agar wo aage nahi poochta, **tum khud le aao**. Ye section tumhare paas tayyar hona chahiye.

---

## 10. Payments — "isme add karte to kaise karte?"

> ⚠️ Ye abhi **bana nahi hai**. Par ye sabse common follow-up hai ("aage kya?"), aur iska achha jawab dena ye dikhata hai ki tum feature nahi, **problem** sochte ho.
>
> Bolne ka tarika: "banaya nahi hai abhi, par design soch chuka hoon —" phir neeche wala.

### Asli problem gateway nahi hai

> "Stripe ya Razorpay integrate karna docs padh ke koi bhi kar leta hai. Asli problem wo hai jo payments **majboori me** laate hain:
>
> **Paisa kat gaya, par booking fail ho gayi.**
>
> Ye classic **dual-write problem** hai — do systems, payment gateway aur mera database, dono ko consistent rakhna jab dono me se koi bhi kabhi bhi fail ho sakta hai."

### Seat ka state machine badal jayega

> "Abhi flow hai: `available → locked → booked`, aur booked wahi API call pe hota hai.
>
> Payments ke baad:
>
> ```
> available → locked → payment_pending → booked
>                            ↓
>                   (fail/timeout) → available
> ```
>
> Seat **webhook aane tak** booked nahi hogi."

### ⭐ Webhook source of truth, browser redirect nahi

Ye is poore jawab ka sabse important hissa hai.

> "Payment ke baad gateway user ko mere site pe redirect karta hai. Us redirect par bharosa **nahi** kar sakta — do wajah:
>
> 1. User payment karke tab band kar de, to redirect aata hi nahi — par paisa kat chuka hai. Booking honi chahiye.
> 2. Koi seedha wo redirect URL hit kar de, to bina paise ke booking ban jayegi.
>
> Isliye asli confirmation **webhook** se aati hai — server-to-server, aur uska **signature verify** hota hai. Redirect sirf UI ke liye hai: 'thank you' page dikhane ke liye, decision lene ke liye nahi."

### Idempotency yahan aur zaroori ho jaati hai

> "Mere paas idempotency keys pehle se hain (Phase 9). Payments ke bina wo 'double-click se do booking na ho' tha.
>
> Payments ke saath wahi cheez '**do baar paisa na kate**' ban jaati hai — same code, kahin zyada weight.
>
> Aur webhooks khud bhi **at-least-once** hote hain — gateway same event do baar bhej sakta hai agar pehla response miss ho jaye. To webhook handler ko bhi idempotent hona hi padta hai. Event id ko key bana ke wahi Redis wala pattern lagega."

### Card details kabhi apne server pe nahi

> "Hosted checkout use karta — Stripe Checkout ya Payment Link. Card details mere backend ko chhuti hi nahi.
>
> Warna main **PCI-DSS scope** me aa jata, jo ek portfolio project ke liye bhi galat design hai — aur production me to compliance ka poora bojh aa jata."

### Bina keys ke bhi project chalna chahiye

> "Interviewer mera repo clone karega to uske paas meri Stripe keys nahi hongi. Isliye `STRIPE_KEY` khali ho to ek 'Simulate payment' path dikhega jo wahi webhook internally fire karta hai.
>
> Bilkul wahi pattern jo maine Google OAuth me use kiya — credentials na ho to button chhup jata hai, baaki sab chalta rehta hai."

### Follow-ups jo aayenge

| Sawaal | Jawab |
|---|---|
| "Webhook late aaya to?" | Seat `payment_pending` me apni TTL ke saath baithi rehti hai. TTL nikal gayi aur webhook nahi aaya to seat release, aur payment refund flow trigger. Warna ek failed payment seat hamesha ke liye block kar deta |
| "Webhook do baar aaya to?" | Event id se idempotent — dusri baar wahi stored result, dobara kaam nahi |
| "Webhook aaya hi nahi to?" | Gateway retry karta hai. Uske upar ek reconciliation job — pending payments ko gateway se poochh ke settle karna. Sirf webhook pe bharosa nahi |
| "Refund kaise?" | Booking cancel → refund API → refund webhook aane par hi booking `refunded`. Wahi asymmetry: paisa hum bhejte hain, confirmation gateway deta hai |
| "Ek transaction me dono kyu nahi?" | Kyunki gateway meri database transaction me nahi hai. External call ko DB transaction ke andar rakhna sabse aam galti hai — transaction network call jitni der khuli rehti hai |


---

## 11. Dynamic pricing — "quote ek waada hai"

Ye section chhota lagta hai par **interview me bahut chalta hai**, kyunki
isme ek aisa faisla hai jo zyada log miss kar dete hain.

### "Dynamic pricing kaise kiya?"

> "Formula sabse boring hissa hai — `multiplier = 1 + (sold/total) x demand_factor`, ek `max_surge` cap ke saath. Do faisle interesting the.
>
> **Pehla:** seat ka `price` column kabhi update nahi hota. Wo BASE hai, aur current price hamesha usse calculate hota hai.
>
> **Dusra:** jab user seat hold karta hai, tabhi uska price bhi lock ho jata hai."

### ⭐ "Base price update karne me kya problem thi?"

Ye wo sawaal hai jahan char alag wajah gina sakte ho — aur char wajah ek se
kahin behtar sunai deti hai:

> "Char cheezein tootti:
>
> 1. **History mit jati** — purani booking me ₹800 likha hai, seat pe ₹1400. 'Original price kya tha' ka jawab kahin nahi bachta.
> 2. **Write amplification** — ek booking par 500 seats ka UPDATE. Flash sale me 500 bookings matlab 250,000 row updates.
> 3. **Naya race condition** — do parallel bookings ab price update pe bhi ladtin. Maine ek nayi contention point paida kar di hoti.
> 4. **Compounding** — `price x 1.1` baar-baar lagega to ₹800 → ₹880 → ₹968… wo formula ka matlab hi nahi tha.
>
> Ab teen alag facts teen alag jagah hain: base price seat pe, multiplier calculated, aur jo actually charge hua wo booking pe."

### ⭐⭐ "Checkout ke beech price badal gaya to?"

**Ye poore feature ka sabse achha sawaal hai. Isko tayyar rakho.**

> "Ye maine feature banane se pehle socha, kyunki ye correctness ka sawaal hai, UX ka nahi.
>
> User ₹1000 dekhta hai, seat hold karta hai, payment page pe jata hai. Beech me 4 seats aur bik gayi. Agar checkout ₹1400 charge kar de — to maine user se chup-chaap zyada paisa liya. Wo bug nahi, wo dhokha hai.
>
> Solution: quote **hold ke saath lock** ho jata hai. `seats.held_price` column me jo price dikhaya tha wahi likh dete hain. Hold chhutne ya expire hone par NULL ho jata hai."

**Follow-up jo aayega — "column kyu, calculate kyu nahi?"**

> "Kyunki 'us waqt price kya tha' ko baad me compute kiya hi nahi ja sakta — demand tab tak badal chuki hoti hai. Quote ek waada hai, aur waade store karne padte hain, derive nahi hote."

**Follow-up 2 — "user hold-release-hold karke sasta price pakad le to?"**

> "Isliye release par `held_price` NULL kar dete hain, aur lazy expiry cleanup me bhi. Naya hold matlab naya price. Ye maine specifically test kiya hai — `test_releasing_a_hold_drops_the_locked_price`."

### "Payments me isse related koi bug mila?"

Ye khud bata do — self-caught bug batana bahut strong lagta hai:

> "Ek chhupi hui galti thi. Maine pehle `price_now()` ko do baar call kiya tha — ek baar payment row banane me, ek baar gateway session banane me. Beech me hold expire ho sakta tha, aur tab gateway ₹1400 charge karta jabki mere DB me ₹1000 likha hota.
>
> Wo mismatch reconciliation job me hi pakda jata — tab tak user ka paisa kat chuka hota. Ab ek hi baar quote nikalta hai aur dono jagah wahi jata."

### "WebSocket pe price update kaise bheja?"

> "Seedha rasta hota har seat ka naya price broadcast karna — par 500 seats wale event me ek booking = 500 messages. Flash sale me wo khud ek DoS hai.
>
> Asli baat ye hai ki multiplier **poore event ka ek hi hai**, aur base price frontend ke paas pehle se hai. To ek event-level `pricing_update` message bhejta hoon. Ek message vs 500, result same."

**Follow-up — "phir frontend base x multiplier kar leta na?"**

Yahan ek achhi detail hai jo interviewer ko surprise karti hai:

> "Maine try kiya tha, par nahi rakha. JavaScript ka `Math.round(100.5)` 101 deta hai, Python ka `round(100.5)` 100 — banker's rounding. Ties par dono alag jawab dete hain.
>
> Matlab UI ₹1010 dikhata aur server ₹1000 charge karta. ₹10 chhota lagta hai, par is feature ki poori buniyaad hi 'jo dikha wahi kata' hai — wahi toot jata.
>
> To banner turant update hota hai (wahi user dekhta hai), aur exact prices 400ms debounce ke baad server se aate hain."

### "UI me urgency kaise dikhayi?"

> "Sirf jab sach ho. 'N seats left at this price' tabhi dikhta hai jab server ne actually calculate kiya ho ki N seats me price badhega — aur wo loop chala kar nikalta hai, formula se andaza nahi lagata.
>
> Agar price abhi nahi badhne wala, ya max surge aa chuka hai, to wo line **dikhati hi nahi**. Jhoothi urgency banane se behtar khaali jagah hai. Wahi 'price locked' badge ke saath — wo tabhi aata hai jab market price actually locked price se upar ho."

### Rapid fire

| Sawaal | Ek-line jawab |
|---|---|
| Default on ya off? | Off. Free community meetup pe surge bhaddha lagta hai — organizer khud on kare |
| `max_surge` kyu chahiye? | Bina cap ke pricing bekaboo lagti hai. Aur `demand_factor` ka upper bound bhi hai — galti se 50 type ho jaana bahut mehnga |
| Organizer base price edit kar sakta hai? | Nahi. Wo purani bookings ko jhootha bana deta. Surge knobs edit kar sakta hai — wo sirf aage ki bookings pe lagte hain |
| Multiplier cache kyu nahi kiya? | 2 count queries hain, per-seat nahi. Aur galat cached price dikhana us saving se kahin mehnga hai |
| Time-based surge kyu nahi? | Bina asli historical data ke wo sirf random constants hote. Jo nahi maapa, use claim nahi karta |

---

## 12. Traps — jahan "haan" bolna galat hai

### "Kafka use kar sakte the na?"

> "Kar sakta tha, par yahan galat fit hota. Kafka tab chahiye jab events **durable aur replayable** hone chahiye — payment ledger jaisa.
>
> Yahan seat locks 5 minute jeete hain aur mar jaate hain. Unhe replay karne ka koi matlab hi nahi. Redis pub/sub is kaam ke liye sahi size ka tool hai, aur wo pehle se stack me tha.
>
> Haan, jab payments add karunga — tab durable event log ki asli zaroorat padegi."

### "Microservices me kyu nahi toda?"

> "Kyunki ye ek team, ek deployment, aur ek database wala system hai. Microservices banata to distributed transactions ka dard mol le leta — aur mera poora core problem hi transactional consistency hai. Wo isse aasan nahi, mushkil ho jata.
>
> Modular monolith rakha hai — routers alag hain, layers साफ hain. Zaroorat padne par nikalna aasan rahega."

### "Isse simple nahi kar sakte the?"

> "Ek layer chhod sakta tha — Redis. Aur wo version + unique index ke saath bhi correct rehta. Par tab har request DB tak jaati.
>
> Baaki do layers me se ek bhi hataunga to ya to correctness jayegi ya insurance. Ye teen jaan-boojh ke hain, ek dusre ka duplicate nahi."

---

## 13. Rapid fire

| Sawaal | Ek-line jawab |
|---|---|
| Seat kis state me ho sakti hai? | available, locked, booked — check constraint DB me hai |
| Lock kitni der? | 5 min, config me, Redis TTL se |
| Do tab me same user? | Dusra tab `already_owned` wala 200 pata hai, naya lock nahi |
| Booking cancel pe seat? | Booking row `cancelled` hoti hai, delete nahi — partial index sirf `confirmed` pe hai, to seat dubara bik sakti hai |
| Migration tool? | Alembic, aur autogenerate ki file mai padhta hoon before apply |
| Test kitne? | 29 — auth, RBAC, rate limit, concurrency. Asli HTTP se, mock nahi |
| Mock kyu nahi? | Race conditions mock me dikhti hi nahi. Jo bug load test ne pakda, wo mocked test kabhi na pakadta |
| Bots kaise roke? | Redis token bucket, per-user aur per-email — per-IP nahi, wo edge (nginx/CDN) ka kaam hai |
| Roles kaise? | Teen flat roles + `require_role`. Par role check aur **ownership** check alag hain — organizer hone se koi bhi event tumhara nahi ho jata |
| Double-click se do booking? | `Idempotency-Key` — wahi key dubara aaye to naya kaam nahi, pehla jawab wapas |
| Frontend counts kahan se? | Seats array se derive hote hain, server se nahi — WebSocket update pe apne aap sahi |
| Docker me Redis persist? | Nahi, jaan-boojh ke. Sirf temporary locks hain |
| CI hai? | Abhi nahi, roadmap me hai |

---

## 14. Whiteboard — architecture aise banao

Isi order me banao, bolte hue:

```
1.  Browser ─── HTTP ──▶ FastAPI
2.                        │
3.                        ├──▶ Redis    (lock: SET NX EX 300)
4.                        │
5.                        └──▶ Postgres (version + unique index)
6.
7.  Browser ◀── WebSocket ── FastAPI ◀── Redis pub/sub
```

Bolte waqt teen baatein zaroor:
1. **Redis pehle** — "5000 me se 4999 yahin ruk jaati hain"
2. **Postgres aakhri faisla** — "atomic UPDATE aur unique index"
3. **Pub/sub arrow** — "isi se multi-worker pe kaam karta hai"

---

## 15. Tum kya poochho

Interview do-tarfa hai. Ye poochne se pata chalta hai ki tum production ke bare me sochte ho:

- "Aap log concurrency issues production me kaise pakadte ho — load testing pipeline me hai ya incident ke baad pata chalta hai?"
- "Aapke system me abhi sabse bada scaling bottleneck kya hai?"
- "Optimistic locking use karte ho kahin? Kaise decide karte ho kab pessimistic chahiye?"
- "Naye engineer ko production tak pahunchne me kitna time lagta hai?"

---

## Aakhri baat

Teen cheezein hain jo is project ko normal projects se alag karti hain. Har interview me ye teeno aani chahiye:

1. **Layered defence, aur har layer ka reason** — "Redis speed ke liye, DB correctness ke liye" wali line
2. **Numbers** — 200 users, 8154 requests, 0 failures, exactly 1 booking
3. **Load test se mile teen bug** — khaaskar `pg_stat_activity` wala debugging

Aur ek line jo kabhi mat bhoolna:

> "Correctness maanni nahi hoti, maapni hoti hai."

---

## Related

- [roadmap.md](roadmap.md) — kya ban chuka, kya baaki
- [Phase 4 — Redis Locking](phases/04-redis-locking.md) — locking ka design
- [Phase 6 — Load Testing](phases/06-load-testing.md) — load test aur pehla bug
- [Phase 7 — Auth + Google OAuth](phases/07-auth-google-oauth.md) — auth + baaki do bug
- [Phase 14 — Dynamic Pricing](phases/14-dynamic-pricing.md) — price lock ka poora design
- [testing.md](reference/testing.md) — sab kuch demo karne ke commands
