# Basis data MPL Indonesia

Basis data relasional SQLite berisi sembilan musim MPL Indonesia (S10&ndash;S18):
680 laga, 1.611 game, 32.219 pick/ban, 320 pemain, dan 135 hero.

```bash
python database/build.py            # bangun ulang dari dataset/*.csv
python database/build.py --periksa  # periksa basis data yang sudah ada
sqlite3 database/mpl.db             # buka
```

## Kenapa SQLite

Untuk data sebesar ini (4,9 MB) dan pemakaian riset, server basis data justru
menambah kerja tanpa menambah manfaat.

| | |
|---|---|
| **Satu berkas** | bisa disalin, di-ZIP, dilampirkan ke makalah, atau ditaruh di Git |
| **Tanpa pemasangan** | sudah ada di dalam Python, R, dan hampir semua bahasa |
| **SQL standar** | kueri yang ditulis di sini jalan juga di PostgreSQL dengan sedikit penyesuaian |
| **Bisa dibaca apa saja** | `pandas.read_sql`, `DBI::dbConnect` di R, DB Browser for SQLite, DBeaver |

Semua tabel memakai mode **`STRICT`** (butuh SQLite &ge; 3.37). Tanpa itu SQLite
menerima teks di kolom `INTEGER` tanpa protes &mdash; persis jenis kesalahan yang
paling sulit ditemukan belakangan.

```python
import sqlite3, pandas as pd
con = sqlite3.connect("database/mpl.db")
df = pd.read_sql("SELECT * FROM v_klasemen WHERE season = 17", con)
```

## Isi folder

| Berkas | Isi |
|---|---|
| `schema.sql` | DDL: 16 tabel, kunci, CHECK, indeks |
| `views.sql` | 9 view analitik |
| `build.py` | pembangun + pemeriksa |
| `queries/` | 6 kueri riset contoh |
| `mpl.db` | hasil build (bisa dihasilkan ulang kapan saja) |

`mpl.db` adalah **hasil, bukan sumber**. Jangan menyuntingnya langsung: ubah
scraper, `dataset.py`, atau `schema.sql`, lalu build ulang. Dengan begitu tidak
ada perubahan yang cuma hidup di dalam berkas `.db` dan hilang jejaknya.

## Bagan keterkaitan

```
   DIMENSI                    FAKTA

   role ──────────┬──────► roster ◄─────────┐
                  └──────► pemain_musim ◄───┤
                                            │
   tim ◄──────────┬──────► tim_musim ◄──────┤
                  ├──────► roster           │
                  ├──────► pemain_musim     ├── musim
                  ├──────► laga             │
                  ├──────► pick_ban         │
                  └──────► award ◄──────────┤
                                            │
   pemain ◄───────┬──────► roster           │
                  ├──────► pemain_musim     │
                  ├──────► award            │
                  └──────► laga.mvp         │
                                            │
   hero ◄─────────┬──────► hero_musim ◄─────┤
                  ├──────► hero_relasi ◄────┘
                  └──────► pick_ban

   laga ──1:N──► game ──1:N──► pick_ban
```

`pick_ban` menunjuk **`game`**, bukan sekadar `laga` &mdash; pick di game yang
tidak ada adalah hal yang mustahil, dan kunci asing gabungan
`(laga_id, game)` membuatnya mustahil juga di basis data.

## Kamus data

### Dimensi

| Tabel | Baris | Kunci utama | Keterangan |
|---|---|---|---|
| `tim` | 11 | `kode` | organisasi, bukan nama per musim |
| `pemain` | 320 | `pemain_id` | slug halaman Liquipedia |
| `hero` | 135 | `hero` | |
| `role` | 6 | `role` | bentuk kanonik EXP/Jungle/Mid/Gold/Roam/Flex |
| `musim` | 9 | `season` | |

### Fakta

| Tabel | Baris | Kunci utama |
|---|---|---|
| `tim_musim` | 79 | `season` + `kode` |
| `roster` | 809 | `season` + `kode` + `pemain_id` + `grup` |
| `pemain_musim` | 62 | `season` + `kode` + `pemain` |
| `laga` | 680 | `laga_id` |
| `game` | 1.611 | `laga_id` + `game` |
| `pick_ban` | 32.219 | `laga_id` + `game` + `kode` + `jenis` + `urutan` |
| `hero_musim` | 779 | `season` + `hero` |
| `hero_relasi` | 6.292 | `season` + `hero` + `jenis` + `hero_lain` |
| `award` | 611 | `season` + `award` + `pemain_id` |

### Provenans

`meta` mencatat versi skema, waktu build, versi SQLite, dan **sidik SHA-256
seluruh CSV sumber**. Kalau angka di sebuah analisis tidak bisa diulang, sidik
inilah yang memastikan apakah datanya memang berubah.

```sql
SELECT * FROM meta;
SELECT * FROM meta_tabel;
```

## View

| View | Gunanya |
|---|---|
| `v_pemain` | pemain dengan negara terlengkap, jumlah musim, dan gelar |
| `v_klasemen` | klasemen + nasib playoff dalam satu baris |
| `v_laga` | laga dengan nama tim dan pemenang yang sudah diselesaikan |
| `v_draft` | tiap pick/ban beserta konteks laganya |
| `v_karier_pemain` | rekap karier: musim, tim, gelar, award, MVP |
| `v_hero_meta` | pick rate, ban rate, dan presence per musim |
| `v_h2h` | head-to-head antar tim (dua arah) |
| `v_regu_juara` | regu juara tiap musim, termasuk cadangan |
| `v_statistik_pemain` | KDA musim berjalan + nama tim |

## Kesepakatan yang perlu diketahui

**`NULL` berarti "sumbernya tidak punya", bukan nol.** Ini penting untuk
agregat: `AVG()` melewati `NULL` tapi ikut menghitung `0`.

Contohnya `hero_musim.win_rate`. Hero yang tidak pernah di-pick punya win rate
yang **tidak terdefinisi**, bukan 0%. Dua sumber kita semula tidak sepakat soal
ini &mdash; Liquipedia mengosongkan, id-mpl menulis `0.0` &mdash; dan yang
ditegakkan adalah versi Liquipedia. Kalau tidak, 59 hero yang tidak pernah
di-pick akan menumpuk di dasar peringkat "win rate terburuk".

**`tim_musim.lolos_playoff` punya tiga keadaan:**

| Nilai | Artinya |
|---|---|
| `1` | lolos playoff |
| `0` | ikut musim itu tapi tidak lolos |
| `NULL` | playoff musim itu belum dimainkan (S18) |

Karena itu, menghitung "berapa musim tim ini lolos" harus memakai
`COUNT(lolos_playoff)` sebagai penyebut, bukan `COUNT(*)` &mdash; lihat
`queries/03_dominasi_tim.sql`.

**`roster.juara` bukan award.** Gelar juara melekat pada **regu**, jadi cadangan
ikut terhitung. Tabel `award` berisi penghargaan **perorangan** (MVP, Team of
the Week, Best Talent). Menggabungkan keduanya membuat hitungan "berapa
penghargaan yang dia dapat" jadi salah.

**`hero_relasi` punya dua aturan simetri yang berbeda:**

| `jenis` | Artinya | Simetri |
|---|---|---|
| `rekan` | dua hero satu tim | `(A,B)` dan `(B,A)` **identik** |
| `lawan` | dua hero berhadapan | `(A,B)` dan `(B,A)` **berkebalikan** |

Keduanya tidak boleh dicampur dalam satu agregat.

**`roster` boleh memuat satu orang dua kali dalam satu musim dan tim yang sama.**
Barbossa tercatat sebagai cadangan (DNP) *sekaligus* analis di BTR S15. Karena
itu `grup` ikut menjadi bagian kunci utama.

## Batasan

**Tidak ada hasil per game.** Tabel `game` mencatat durasi, tapi tidak mencatat
siapa yang menang. Akibatnya 32.219 baris `pick_ban` **tidak punya label
menang/kalah**: pertanyaan "draft seperti apa yang menang" belum bisa dijawab
dari baris mentah, hanya lewat agregat di `hero_musim` dan `hero_relasi`.
Datanya ada di HTML sumber dan tinggal diambil &mdash; ini lubang terbesar
dalam dataset ini.

**Tidak ada penghubung pemain &harr; hero.** `pick_ban` mencatat pick milik
*tim*, bukan *pemain*. "Win rate Lancelot-nya Kairi" tidak bisa dijawab, dan
sumbernya memang tidak menyediakan.

**Cakupan kolom tidak rata antar musim.** Jalankan
`queries/06_cakupan_data.sql` lebih dulu sebelum menganalisis apa pun:

| Kolom | Tersedia |
|---|---|
| laga, klasemen, award, roster | S10&ndash;S18 |
| game, pick/ban, hero_musim, hero_relasi | S10&ndash;S17 |
| KDA pemain, gold/damage/objektif tim | S18 saja |
| hasil playoff | S10&ndash;S17 (S18 belum dimainkan) |

**Musim 1&ndash;9 belum ada.** Halamannya tersedia di Liquipedia.

**Statistik RRQ S18 tidak lengkap di sumbernya.** Tabel pemain MPL melewatkan
Excellent99 (cadangan Roam, 2 game), padahal 2 game itu tetap terhitung di total
tim. Jadi `SUM(pemain_musim.kill)` untuk RRQ tidak akan sama dengan
`tim_musim.kill`. Delapan tim lainnya cocok persis.

## Pemeriksaan

`build.py` menolak menyatakan berhasil sebelum empat hal terpenuhi:

1. `PRAGMA integrity_check` &mdash; berkasnya tidak rusak
2. `PRAGMA foreign_key_check` &mdash; tidak ada baris yatim
3. jumlah baris tiap tabel **sama dengan CSV asalnya** &mdash; CHECK yang gagal
   membatalkan INSERT-nya, jadi selisih di sini menangkap baris yang diam-diam
   tidak masuk
4. kesembilan view benar-benar bisa dijalankan &mdash; view yang salah ketik
   baru ketahuan saat dipakai, bukan saat dibuat

Penegakan skema di sini sudah menemukan satu hal yang lolos dari pemeriksa
dataset: `roster` ternyata tidak pernah diuji keunikan kunci utamanya, dan
memang ada satu baris kembar.

## Atribusi

Data S10&ndash;S17 dari [Liquipedia](https://liquipedia.net/mobilelegends/),
berlisensi [CC-BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
Data musim berjalan dari [id-mpl.com](https://id-mpl.com). Karya turunan dari
basis data ini wajib mempertahankan atribusi yang sama.
