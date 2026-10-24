# GOLD restobar — Kassa jurnali

Kafe kassasi uchun oddiy Windows dasturi: har bir kirim/chiqim yozuvi
**avval kompyuterga** saqlanadi, so'ngra **Google Sheets**'dagi
`Kassa jurnali` varag'iga avtomatik yuboriladi.

- Qog'oz jurnal ko'rinishidagi jadval — summa to'g'ri katakchaga yoziladi
- To'lov usuli: Kirim — Naqd / Click / Terminal, Chiqim — Naqd / Click
- Bugungi Kirim, Chiqim va Qoldiq (kirim − chiqim) real vaqtda
- Kiritilgan, lekin **Enter** bosilmagan qatorlar ham saqlanadi (draft)
- Internet yo'q bo'lsa ham yozuv yo'qolmaydi (SQLite navbat + "Qayta yuborish")
- `service_account.json` kaliti orqali Google Sheets'ga yozadi
- Brauzer versiyasi ham bor: `python web_app.py` → http://localhost:8000

Ustunlar (Sheets va desktop/web uchun bir xil):
`Sana | Vaqt | Kassir | Turi | To'lov usuli | Summa | Izoh | UID`

---

## 1. Google Sheets'ni sozlash (bir marta)

1. [Google Cloud Console](https://console.cloud.google.com/)'da loyiha yarating.
2. **Google Sheets API**'ni yoqing (APIs & Services → Library).
3. **Service account** yarating: IAM & Admin → Service Accounts → Create.
4. Shu service account uchun **JSON kalit** yuklab oling
   (Keys → Add key → Create new key → JSON).
   Fayl nomini `service_account.json` qilib qo'ying.
5. Kalit fayl ichidagi `client_email` manzilini nusxalang
   (masalan `kassa@loyiha.iam.gserviceaccount.com`).
6. Google Sheets faylini oching va **Ulashish (Share)** orqali shu
   `client_email`'ga **Editor** huquqini bering.

Spreadsheet ID va varaq nomi dasturda allaqachon yozilgan:

```
Spreadsheet ID: 1g3sb3swmuWLHGuMt0_hoYwIf0myHH8q-pKRF5l9vAn8
Varaq nomi:     Kassa jurnali
```

Agar varaq mavjud bo'lmasa, dastur uni **o'zi yaratadi** va sarlavha
qatorini yozadi.

> Kalit faylni dastur papkasiga (`.exe` yoniga) qo'ying yoki dastur
> ichidagi **Sozlamalar → Tanlash…** orqali tanlang.

---

## 2. Ishga tushirish (Python bilan)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Birinchi ishga tushirishda:

1. `service_account.json` faylini dastur papkasiga qo'ying (yoki Sozlamalar orqali tanlang).
2. **Sozlamalar → Test ulanish** bilan aloqani tekshiring.

### Web versiyasi (brauzerda)

```bat
python web_app.py
```

Brauzer avtomatik ochiladi: **http://localhost:8000** — xuddi shu kassa,
faqat veb-interfeysda. Server faqat `localhost`'ga tinglaydi (tarmoqdan
kirish yopiq) va boshqa saytlardan kelgan so'rovlar rad etiladi.

---

## 3. Bitta `.exe` yasash

Eng oson yo'l — `build.bat` faylini ikki marta bosing (yoki terminalda):

```bat
build.bat
```

Qo'lda bajarish:

```bat
.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean kassa.spec
```

Natija: **`dist\GOLD_Kassa.exe`** — bitta fayl, o'rnatish talab qilmaydi.

**Muhim:** `dist` papkasiga `service_account.json` faylini ham nusxalang:

```
dist\
  GOLD_Kassa.exe
  service_account.json
```

`exe`'ni istalgan papkaga (masalan `C:\GOLD_Kassa\`) ko'chirish mumkin.
Dastur o'z ma'lumotlarini (`kassa.db`, `settings.json`, `xato.log`)
o'sha papkada saqlaydi. Agar papkaga yozish huquqi bo'lmasa
(masalan `Program Files`), ma'lumotlar `%LOCALAPPDATA%\GOLD_Kassa\`'ga o'tadi.

---

## 4. Kassir uchun qisqa qo'llanma

1. **Kassir ismi** bir marta yoziladi — keyingi safar o'zi eslab qoladi.
2. Jurnal jadvalining **so'nggi (bo'sh) qatoriga** summani to'g'ri
   ustunga yozing (masalan `15000` yoki `1 250 000`).
3. Kerak bo'lsa **izoh** yozing (masalan `stol 5`).
4. **Enter** (yoki **Ctrl+Enter**) — yozuv saqlanadi va yuboriladi.

| Jurnal ustuni | Nima uchun |
|---|---|
| KIRIM — Naqd / Click / Terminal | kirim summasi |
| KIRIM — Comment | kirim izohi |
| CHIQIM — Naqd / Click | chiqim summasi |
| CHIQIM — Comment | chiqim izohi |

Vaqt dastur tomonidan qo'yiladi — uni o'zgartirib bo'lmaydi (audit).

Foydali tugmalar: `Enter` — qo'shish, `Ctrl+R` — qayta yuborish.

Pastdagi holat qatorida:

- `Yuborilgan` — Google Sheets'ga yozildi
- `Kutilmoqda` — internet yo'q, navbatda turibdi
- `Xato` — yuborilmadi (sichqonchani ustiga olib borsangiz sabab ko'rinadi)

Internet qaytganda dastur navbatni **o'zi** yuboradi (har 60 soniyada),
yoki **Qayta yuborish** tugmasini bosing.

---

## 5. Fayllar

| Fayl | Vazifasi |
|---|---|
| `main.py` | Kirish nuqtasi, log sozlash |
| `ui.py` | Asosiy oyna (qog'oz jurnal jadvali, hisob, draftlar) |
| `settings_dialog.py` | Sozlamalar oynasi, "Test ulanish" |
| `style.py` | Dizayn tokenlari va QSS |
| `config.py` | Doimiylar, papkalar, ustunlar |
| `storage.py` | SQLite navbat (`pending` / `sending` / `sent`) |
| `sheets.py` | Google Sheets klienti, xato xabarlari |
| `sync.py` | Fon oqimida yuborish + dublikat himoyasi (UID) |
| `settings.py` | `settings.json` (kalit, kassir, varaq) |
| `helpers.py` | Sana/vaqt/summa formatlash |
| `backup.py` | Davriy yedek nusxalar (`backups/`) |
| `drafts.py` | Enter bosilmagan qatorlar (draft) saqlash |
| `web_app.py` | Brauzer versiyasi (HTTP server + JSON API) |
| `journal.html` | Brauzer versiyasi sabloni |
| `tests/` | 50 dan ortiq test (`unittest` / `pytest`) |

Dastur yonida yaratiladi: `settings.json` (sozlamalar), `xato.log` (xatolar),
`backups/` (yedek nusxalar). Baza esa tizim papkasida saqlanadi:
`%LOCALAPPDATA%\GOLD_Kassa\state\kassa.db` — u OneDrive kabi sinxronlash
tufayli hech qachon buzilmaydi.

---

## 6. Muammolar

| Xabar | Yechim |
|---|---|
| `service_account.json topilmadi` | Kalit faylni `.exe` yoniga qo'ying yoki Sozlamalar orqali tanlang |
| `Google'dan ruxsat yo'q … Editor huquqi bering` | Spreadsheet'ni `client_email` bilan bo'lishing |
| `Google Sheets topilmadi` | Spreadsheet ID xato yoki fayl bo'lishilmagan |
| `Internet aloqasi yo'q …` | Yozuv saqlangan; internet qaytgach "Qayta yuborish" |
| `Kalit fayl o'qilmadi` | JSON faylni qayta yuklab oling (buzilgan) |

Batafsil xatolar: dastur papkasidagi `xato.log`.

---

## 7. Testlar

```bat
.venv\Scripts\activate
python -m unittest discover -s tests -t . -v
```

Testlar tarmoqqa ulanmaydi (soxta klient ishlatiladi) va oyna
`offscreen` rejimda tekshiriladi.
