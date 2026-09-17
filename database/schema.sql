-- =============================================================================
-- MPL Indonesia — skema basis data relasional
-- =============================================================================
-- Dibangun dari dataset/*.csv oleh build.py. JANGAN disunting langsung di
-- berkas .db: semua perubahan struktur ditulis di sini lalu di-build ulang,
-- supaya basis datanya selalu bisa dihasilkan ulang dari sumber.
--
-- Catatan rancangan
-- -----------------
-- * Semua tabel STRICT (butuh SQLite >= 3.37). Tanpa itu SQLite menerima teks
--   di kolom INTEGER tanpa protes — persis jenis kesalahan yang paling sulit
--   ditemukan belakangan.
-- * Tidak ada BOOLEAN di SQLite; dipakai INTEGER 0/1 dengan CHECK.
-- * Kunci asing memakai ON DELETE RESTRICT: baris induk tidak boleh hilang
--   sementara anaknya masih menunjuk. ON UPDATE CASCADE dipakai supaya kode
--   tim yang berganti ikut turun ke seluruh tabel anak.
-- * CHECK diturunkan dari nilai yang BENAR-BENAR ada di data, bukan dari
--   dugaan. Batas yang terlalu ketat akan pecah begitu musim baru masuk.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- =============================================================================
-- DIMENSI — entitas yang dirujuk tabel lain
-- =============================================================================

CREATE TABLE tim (
    kode  TEXT PRIMARY KEY,
    nama  TEXT NOT NULL,
    logo  TEXT NOT NULL
) STRICT;

CREATE TABLE pemain (
    pemain_id TEXT PRIMARY KEY,           -- slug halaman Liquipedia
    nama      TEXT NOT NULL,
    negara    TEXT
) STRICT;

CREATE TABLE hero (
    hero TEXT PRIMARY KEY
) STRICT;

-- Dua sumber menulis role dengan ejaan berbeda ("Jungle" di roster Liquipedia,
-- "JUNGLE" di statistik MPL). Tabel ini yang menjadi bentuk kanoniknya, jadi
-- roster dan statistik pemain bisa di-JOIN lewat role tanpa perlu menormalkan
-- ulang di tiap kueri.
CREATE TABLE role (
    role   TEXT PRIMARY KEY,
    urutan INTEGER NOT NULL UNIQUE        -- urutan posisi di peta, bukan abjad
) STRICT;

CREATE TABLE musim (
    season        INTEGER PRIMARY KEY CHECK (season > 0),
    sumber        TEXT    NOT NULL CHECK (sumber IN ('liquipedia', 'id-mpl.com')),
    jumlah_tim    INTEGER NOT NULL CHECK (jumlah_tim > 0),
    laga_reguler  INTEGER NOT NULL CHECK (laga_reguler >= 0),
    laga_playoff  INTEGER NOT NULL CHECK (laga_playoff >= 0),
    juara_kode    TEXT REFERENCES tim (kode)
                       ON DELETE RESTRICT ON UPDATE CASCADE,
    mvp_pemain_id TEXT REFERENCES pemain (pemain_id)
                       ON DELETE RESTRICT ON UPDATE CASCADE
) STRICT;

-- =============================================================================
-- FAKTA — musim
-- =============================================================================

CREATE TABLE tim_musim (
    season            INTEGER NOT NULL REFERENCES musim (season)
                              ON DELETE RESTRICT ON UPDATE CASCADE,
    kode              TEXT    NOT NULL REFERENCES tim (kode)
                              ON DELETE RESTRICT ON UPDATE CASCADE,
    peringkat         INTEGER NOT NULL CHECK (peringkat > 0),
    main              INTEGER NOT NULL CHECK (main >= 0),
    menang            INTEGER NOT NULL CHECK (menang >= 0),
    kalah             INTEGER NOT NULL CHECK (kalah >= 0),
    win_rate          REAL    NOT NULL CHECK (win_rate BETWEEN 0 AND 100),
    game_menang       INTEGER NOT NULL CHECK (game_menang >= 0),
    game_kalah        INTEGER NOT NULL CHECK (game_kalah >= 0),
    game_win_rate     REAL    NOT NULL CHECK (game_win_rate BETWEEN 0 AND 100),
    belum_main        INTEGER NOT NULL CHECK (belum_main >= 0),

    -- Statistik objektif: hanya ada dari situs resmi MPL, jadi hanya terisi
    -- untuk musim berjalan. NULL di sini berarti "sumbernya tidak punya",
    -- bukan nol.
    kill              INTEGER CHECK (kill   >= 0),
    mati              INTEGER CHECK (mati   >= 0),
    assist            INTEGER CHECK (assist >= 0),
    gold              INTEGER CHECK (gold   >= 0),
    damage            INTEGER CHECK (damage >= 0),
    lord              INTEGER CHECK (lord   >= 0),
    turtle            INTEGER CHECK (turtle >= 0),
    tower             INTEGER CHECK (tower  >= 0),

    -- lolos_playoff punya TIGA keadaan yang berbeda artinya:
    --   1    = lolos
    --   0    = ikut musimnya tapi tidak lolos
    --   NULL = playoff musim itu memang belum dimainkan
    lolos_playoff     INTEGER CHECK (lolos_playoff IN (0, 1)),
    hasil_playoff     TEXT,
    peringkat_playoff INTEGER CHECK (peringkat_playoff > 0),

    PRIMARY KEY (season, kode),
    CHECK (main = menang + kalah),
    CHECK ((hasil_playoff IS NULL) = (peringkat_playoff IS NULL)),
    CHECK (lolos_playoff IS NOT 1 OR hasil_playoff IS NOT NULL)
) STRICT;

CREATE TABLE roster (
    season       INTEGER NOT NULL REFERENCES musim (season)
                         ON DELETE RESTRICT ON UPDATE CASCADE,
    kode         TEXT    NOT NULL REFERENCES tim (kode)
                         ON DELETE RESTRICT ON UPDATE CASCADE,
    pemain_id    TEXT    NOT NULL REFERENCES pemain (pemain_id)
                         ON DELETE RESTRICT ON UPDATE CASCADE,
    grup         TEXT    NOT NULL CHECK (grup IN ('Main', 'Subs', 'Former', 'Staff')),
    role         TEXT REFERENCES role (role)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    negara       TEXT,
    catatan      TEXT,
    nama_periode TEXT    NOT NULL,        -- nama tim SAAT itu, bukan nama sekarang

    -- Gelar juara melekat pada REGU, jadi cadangan ikut terhitung. Sengaja
    -- dipisah dari tabel award, yang isinya penghargaan perorangan.
    juara        INTEGER NOT NULL DEFAULT 0 CHECK (juara IN (0, 1)),

    -- `grup` ikut jadi kunci karena satu orang bisa menyandang dua peran di
    -- regu yang sama pada musim yang sama: Barbossa tercatat sebagai cadangan
    -- (DNP) SEKALIGUS analis di BTR S15. Tanpa `grup`, salah satu barisnya
    -- harus dibuang — padahal keduanya benar.
    PRIMARY KEY (season, kode, pemain_id, grup),
    -- Staf bukan pemain: tidak punya role dan tidak menerima gelar.
    CHECK (grup <> 'Staff' OR (role IS NULL AND juara = 0))
) STRICT;

CREATE TABLE pemain_musim (
    season      INTEGER NOT NULL REFERENCES musim (season)
                        ON DELETE RESTRICT ON UPDATE CASCADE,
    kode        TEXT    NOT NULL REFERENCES tim (kode)
                        ON DELETE RESTRICT ON UPDATE CASCADE,
    pemain      TEXT    NOT NULL,         -- ejaan menurut situs resmi MPL
    -- Boleh NULL: sebagian pemain sudah bermain menurut MPL tapi belum
    -- tercatat di Liquipedia, jadi belum punya halaman. Memaksakan id di sini
    -- berarti mengarang keterkaitan yang belum ada.
    pemain_id   TEXT REFERENCES pemain (pemain_id)
                     ON DELETE RESTRICT ON UPDATE CASCADE,
    role        TEXT REFERENCES role (role)
                     ON DELETE RESTRICT ON UPDATE CASCADE,
    main        INTEGER NOT NULL CHECK (main   >= 0),
    kill        INTEGER NOT NULL CHECK (kill   >= 0),
    kill_rata   REAL    NOT NULL CHECK (kill_rata   >= 0),
    mati        INTEGER NOT NULL CHECK (mati   >= 0),
    mati_rata   REAL    NOT NULL CHECK (mati_rata   >= 0),
    assist      INTEGER NOT NULL CHECK (assist >= 0),
    assist_rata REAL    NOT NULL CHECK (assist_rata >= 0),
    kda         REAL    NOT NULL CHECK (kda >= 0),
    partisipasi REAL    NOT NULL CHECK (partisipasi BETWEEN 0 AND 100),

    PRIMARY KEY (season, kode, pemain)
) STRICT;

-- =============================================================================
-- FAKTA — pertandingan
-- =============================================================================

CREATE TABLE laga (
    laga_id       TEXT PRIMARY KEY,       -- S17R001 / S17P003
    season        INTEGER NOT NULL REFERENCES musim (season)
                          ON DELETE RESTRICT ON UPDATE CASCADE,
    stage         TEXT    NOT NULL CHECK (stage IN ('reguler', 'playoff')),
    week          INTEGER CHECK (week > 0),
    ronde         TEXT,
    tanggal       TEXT    NOT NULL CHECK (tanggal LIKE '____-__-__'),
    jam           TEXT    NOT NULL CHECK (jam LIKE '__:__'),
    timestamp     INTEGER CHECK (timestamp > 0),
    kode1         TEXT    NOT NULL REFERENCES tim (kode)
                          ON DELETE RESTRICT ON UPDATE CASCADE,
    kode2         TEXT    NOT NULL REFERENCES tim (kode)
                          ON DELETE RESTRICT ON UPDATE CASCADE,
    skor1         INTEGER NOT NULL CHECK (skor1 >= 0),
    skor2         INTEGER NOT NULL CHECK (skor2 >= 0),
    status        TEXT    NOT NULL CHECK (status IN ('selesai', 'belum main')),
    format        TEXT    CHECK (format IS NULL OR format LIKE 'Bo_'),
    mvp           TEXT,
    sumber        TEXT    NOT NULL CHECK (sumber IN ('liquipedia', 'id-mpl.com')),
    mvp_pemain_id TEXT REFERENCES pemain (pemain_id)
                       ON DELETE RESTRICT ON UPDATE CASCADE,

    CHECK (kode1 <> kode2),
    -- Laga reguler selalu punya pekan; laga playoff selalu punya ronde.
    CHECK ((stage = 'reguler') = (week IS NOT NULL)),
    CHECK ((stage = 'playoff') = (ronde IS NOT NULL)),
    -- Yang belum dimainkan tidak boleh sudah punya skor.
    CHECK (status = 'selesai' OR (skor1 = 0 AND skor2 = 0))
) STRICT;

CREATE TABLE game (
    laga_id TEXT    NOT NULL REFERENCES laga (laga_id)
                    ON DELETE CASCADE ON UPDATE CASCADE,
    game    INTEGER NOT NULL CHECK (game > 0),
    durasi  TEXT    CHECK (durasi IS NULL OR durasi LIKE '__:__'),

    PRIMARY KEY (laga_id, game)
) STRICT;

CREATE TABLE pick_ban (
    laga_id TEXT    NOT NULL,
    game    INTEGER NOT NULL,
    kode    TEXT    NOT NULL REFERENCES tim (kode)
                    ON DELETE RESTRICT ON UPDATE CASCADE,
    jenis   TEXT    NOT NULL CHECK (jenis IN ('pick', 'ban')),
    urutan  INTEGER NOT NULL CHECK (urutan > 0),
    hero    TEXT    NOT NULL REFERENCES hero (hero)
                    ON DELETE RESTRICT ON UPDATE CASCADE,

    PRIMARY KEY (laga_id, game, kode, jenis, urutan),
    -- Menunjuk game, bukan cuma laga: pick di game yang tidak ada mustahil.
    FOREIGN KEY (laga_id, game) REFERENCES game (laga_id, game)
        ON DELETE CASCADE ON UPDATE CASCADE
) STRICT;

-- =============================================================================
-- FAKTA — hero
-- =============================================================================

CREATE TABLE hero_musim (
    season   INTEGER NOT NULL REFERENCES musim (season)
                     ON DELETE RESTRICT ON UPDATE CASCADE,
    hero     TEXT    NOT NULL REFERENCES hero (hero)
                     ON DELETE RESTRICT ON UPDATE CASCADE,
    picks    INTEGER NOT NULL CHECK (picks  >= 0),
    menang   INTEGER NOT NULL CHECK (menang >= 0),
    kalah    INTEGER NOT NULL CHECK (kalah  >= 0),
    win_rate REAL    CHECK (win_rate BETWEEN 0 AND 100),
    bans     INTEGER NOT NULL CHECK (bans     >= 0),
    pick_ban INTEGER NOT NULL CHECK (pick_ban >= 0),

    PRIMARY KEY (season, hero),
    CHECK (picks = menang + kalah),
    CHECK (pick_ban = picks + bans),
    -- Hero yang tidak pernah dipick tidak punya win rate; yang pernah, punya.
    CHECK ((picks = 0) = (win_rate IS NULL))
) STRICT;

CREATE TABLE hero_relasi (
    season    INTEGER NOT NULL REFERENCES musim (season)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    hero      TEXT    NOT NULL REFERENCES hero (hero)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    -- 'rekan'  = satu tim   (Played With)
    -- 'lawan'  = berhadapan (Played Against)
    jenis     TEXT    NOT NULL CHECK (jenis IN ('rekan', 'lawan')),
    hero_lain TEXT    NOT NULL REFERENCES hero (hero)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    peringkat INTEGER NOT NULL CHECK (peringkat > 0),
    jumlah    INTEGER NOT NULL CHECK (jumlah > 0),
    menang    INTEGER NOT NULL CHECK (menang >= 0),
    kalah     INTEGER NOT NULL CHECK (kalah  >= 0),
    win_rate  REAL    NOT NULL CHECK (win_rate BETWEEN 0 AND 100),

    PRIMARY KEY (season, hero, jenis, hero_lain),
    CHECK (hero <> hero_lain),
    CHECK (jumlah = menang + kalah)
) STRICT;

-- =============================================================================
-- FAKTA — penghargaan
-- =============================================================================

CREATE TABLE award (
    season    INTEGER NOT NULL REFERENCES musim (season)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    award     TEXT    NOT NULL,
    kategori  TEXT    NOT NULL CHECK (kategori IN
                      ('musim', 'weekly_mvp', 'weekly_rookie', 'team_of_week')),
    week      INTEGER CHECK (week > 0),
    pemain_id TEXT    NOT NULL REFERENCES pemain (pemain_id)
                      ON DELETE RESTRICT ON UPDATE CASCADE,
    -- Boleh NULL: Best Talent ID/EN diberikan ke kaster, yang tidak bertim.
    kode_tim  TEXT REFERENCES tim (kode)
                   ON DELETE RESTRICT ON UPDATE CASCADE,
    hadiah    TEXT,

    PRIMARY KEY (season, award, pemain_id),
    -- Award mingguan wajib menyebut pekannya; award musim tidak punya pekan.
    CHECK ((kategori = 'musim') = (week IS NULL))
) STRICT;

-- =============================================================================
-- PROVENANS — dari mana dan kapan basis data ini dibangun
-- =============================================================================

CREATE TABLE meta (
    kunci TEXT PRIMARY KEY,
    nilai TEXT NOT NULL
) STRICT;

CREATE TABLE meta_tabel (
    tabel  TEXT PRIMARY KEY,
    baris  INTEGER NOT NULL CHECK (baris >= 0),
    sumber TEXT NOT NULL
) STRICT;

-- =============================================================================
-- INDEKS
-- =============================================================================
-- Kolom kunci utama sudah terindeks sendiri. Yang ditambahkan di sini adalah
-- kolom kunci asing dan jalur kueri yang sering dipakai — tanpa ini, menelusuri
-- "semua laga tim X" harus memindai seluruh tabel.

CREATE INDEX idx_laga_season_stage ON laga (season, stage);
CREATE INDEX idx_laga_kode1        ON laga (kode1);
CREATE INDEX idx_laga_kode2        ON laga (kode2);
CREATE INDEX idx_laga_tanggal      ON laga (tanggal);
CREATE INDEX idx_laga_mvp          ON laga (mvp_pemain_id);

CREATE INDEX idx_pickban_hero      ON pick_ban (hero, jenis);
CREATE INDEX idx_pickban_kode      ON pick_ban (kode, jenis);
CREATE INDEX idx_pickban_game      ON pick_ban (laga_id, game);

CREATE INDEX idx_roster_pemain     ON roster (pemain_id);
CREATE INDEX idx_roster_kode       ON roster (kode);
CREATE INDEX idx_roster_juara      ON roster (juara) WHERE juara = 1;

CREATE INDEX idx_pemain_musim_id   ON pemain_musim (pemain_id);
CREATE INDEX idx_pemain_musim_kode ON pemain_musim (kode);

CREATE INDEX idx_award_pemain      ON award (pemain_id);
CREATE INDEX idx_award_tim         ON award (kode_tim);
CREATE INDEX idx_award_kategori    ON award (kategori, season);

CREATE INDEX idx_tim_musim_kode    ON tim_musim (kode);
CREATE INDEX idx_hero_musim_hero   ON hero_musim (hero);
CREATE INDEX idx_hero_relasi_lain  ON hero_relasi (hero_lain, jenis);
