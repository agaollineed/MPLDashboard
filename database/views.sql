-- =============================================================================
-- View analitik
-- =============================================================================
-- Tabel dasar sengaja dijaga tetap normal dan persis mencerminkan dataset/*.csv.
-- View di berkas ini yang menyediakan bentuk siap-pakai: nama sudah tersambung,
-- pemenang sudah diselesaikan, dan agregat yang sering dibutuhkan sudah jadi.
--
-- Semua diawali v_ supaya jelas mana tabel dan mana turunan.
-- =============================================================================

-- Pemain dengan negara yang paling lengkap.
-- Tabel `pemain` mengambil negara dari baris roster yang pertama ditemui, jadi
-- pemain yang hanya muncul lewat award tidak kebagian. Roster punya negara
-- untuk SEMUA barisnya, jadi dipakai sebagai cadangan.
CREATE VIEW v_pemain AS
SELECT p.pemain_id,
       p.nama,
       COALESCE(p.negara, (SELECT r.negara FROM roster r
                           WHERE r.pemain_id = p.pemain_id AND r.negara IS NOT NULL
                           ORDER BY r.season DESC LIMIT 1)) AS negara,
       (SELECT COUNT(DISTINCT r.season) FROM roster r
        WHERE r.pemain_id = p.pemain_id AND r.grup IN ('Main','Subs')) AS musim_main,
       (SELECT COUNT(*) FROM roster r
        WHERE r.pemain_id = p.pemain_id AND r.juara = 1)                AS gelar
FROM pemain p;

-- Klasemen satu musim: nama tim, rekor, dan nasib playoff dalam satu baris.
CREATE VIEW v_klasemen AS
SELECT tm.season,
       tm.peringkat,
       tm.kode,
       t.nama                                   AS tim,
       tm.main, tm.menang, tm.kalah, tm.win_rate,
       tm.game_menang, tm.game_kalah, tm.game_win_rate,
       tm.lolos_playoff,
       tm.hasil_playoff,
       tm.peringkat_playoff,
       CASE WHEN tm.hasil_playoff = 'Champion' THEN 1 ELSE 0 END AS juara
FROM tim_musim tm
JOIN tim t ON t.kode = tm.kode;

-- Laga dengan nama tim dan pemenang yang sudah diselesaikan.
-- pemenang/pihak_kalah bernilai NULL kalau laganya belum dimainkan atau seri.
CREATE VIEW v_laga AS
SELECT l.laga_id, l.season, l.stage, l.week, l.ronde,
       l.tanggal, l.jam, l.timestamp,
       l.kode1, t1.nama AS tim1, l.skor1,
       l.kode2, t2.nama AS tim2, l.skor2,
       CASE WHEN l.status <> 'selesai' THEN NULL
            WHEN l.skor1 > l.skor2 THEN l.kode1
            WHEN l.skor2 > l.skor1 THEN l.kode2 END AS pemenang,
       CASE WHEN l.status <> 'selesai' THEN NULL
            WHEN l.skor1 > l.skor2 THEN l.kode2
            WHEN l.skor2 > l.skor1 THEN l.kode1 END AS pihak_kalah,
       l.skor1 + l.skor2 AS total_game,
       l.status, l.format, l.mvp_pemain_id, l.sumber
FROM laga l
JOIN tim t1 ON t1.kode = l.kode1
JOIN tim t2 ON t2.kode = l.kode2;

-- Draft lengkap: tiap pick/ban dengan konteks laganya.
--
-- PENTING: tidak ada kolom "game ini dimenangkan siapa". Sumbernya memang tidak
-- menyediakan hasil per game — yang tercatat hanya skor laga. Jadi pertanyaan
-- "draft seperti apa yang menang" belum bisa dijawab dari tabel ini; yang bisa
-- hanyalah lewat agregat di hero_musim. Lihat README bagian Batasan.
CREATE VIEW v_draft AS
SELECT pb.laga_id, pb.game, pb.jenis, pb.urutan, pb.hero,
       pb.kode, t.nama AS tim,
       l.season, l.stage, l.week, l.tanggal,
       CASE WHEN pb.kode = l.kode1 THEN l.kode2 ELSE l.kode1 END AS lawan,
       g.durasi,
       CASE WHEN l.status <> 'selesai' THEN NULL
            WHEN l.skor1 > l.skor2 THEN l.kode1
            WHEN l.skor2 > l.skor1 THEN l.kode2 END AS pemenang_laga
FROM pick_ban pb
JOIN laga l ON l.laga_id = pb.laga_id
JOIN tim  t ON t.kode    = pb.kode
LEFT JOIN game g ON g.laga_id = pb.laga_id AND g.game = pb.game;

-- Ringkasan karier tiap pemain.
CREATE VIEW v_karier_pemain AS
SELECT p.pemain_id,
       p.nama,
       p.negara,
       COUNT(DISTINCT r.season)                             AS musim,
       MIN(r.season)                                        AS musim_awal,
       MAX(r.season)                                        AS musim_akhir,
       COUNT(DISTINCT r.kode)                               AS jumlah_tim,
       SUM(r.juara)                                         AS gelar,
       (SELECT COUNT(*) FROM award a  WHERE a.pemain_id = p.pemain_id) AS award,
       (SELECT COUNT(*) FROM laga  l  WHERE l.mvp_pemain_id = p.pemain_id) AS mvp_laga
FROM v_pemain p
JOIN roster r ON r.pemain_id = p.pemain_id AND r.grup IN ('Main','Subs')
GROUP BY p.pemain_id, p.nama, p.negara;

-- Meta hero per musim, lengkap dengan tingkat kehadirannya di draft.
-- Pembaginya jumlah game musim itu; NULL untuk musim yang belum punya data
-- game (pembagian dengan nol di SQLite menghasilkan NULL, bukan error).
CREATE VIEW v_hero_meta AS
SELECT hm.season, hm.hero,
       hm.picks, hm.bans, hm.pick_ban,
       hm.menang, hm.kalah, hm.win_rate,
       gm.total_game,
       ROUND(100.0 * hm.picks    / gm.total_game, 1) AS pick_rate,
       ROUND(100.0 * hm.bans     / gm.total_game, 1) AS ban_rate,
       ROUND(100.0 * hm.pick_ban / gm.total_game, 1) AS presence
FROM hero_musim hm
LEFT JOIN (SELECT l.season, COUNT(*) AS total_game
           FROM game g JOIN laga l ON l.laga_id = g.laga_id
           GROUP BY l.season) gm ON gm.season = hm.season;

-- Head-to-head antar tim, dihitung dari daftar laga.
-- Tiap pasangan muncul dua kali (A vs B dan B vs A) supaya bisa langsung
-- disaring "semua lawan tim X" tanpa memikirkan urutan kolom.
CREATE VIEW v_h2h AS
SELECT kode AS kode, lawan AS lawan, season, stage,
       COUNT(*)                                   AS laga,
       SUM(menang)                                AS menang,
       COUNT(*) - SUM(menang)                     AS kalah,
       ROUND(100.0 * SUM(menang) / COUNT(*), 1)   AS win_rate
FROM (
    SELECT kode1 AS kode, kode2 AS lawan, season, stage,
           CASE WHEN skor1 > skor2 THEN 1 ELSE 0 END AS menang
    FROM laga WHERE status = 'selesai'
    UNION ALL
    SELECT kode2, kode1, season, stage,
           CASE WHEN skor2 > skor1 THEN 1 ELSE 0 END
    FROM laga WHERE status = 'selesai'
)
GROUP BY kode, lawan, season, stage;

-- Regu juara tiap musim, termasuk cadangannya.
CREATE VIEW v_regu_juara AS
SELECT r.season, r.kode, t.nama AS tim, r.grup, r.role,
       p.pemain_id, p.nama AS pemain, p.negara
FROM roster r
JOIN tim t      ON t.kode = r.kode
JOIN v_pemain p ON p.pemain_id = r.pemain_id
WHERE r.juara = 1;

-- Statistik pemain musim berjalan, sudah dengan nama tim dan urutan role.
CREATE VIEW v_statistik_pemain AS
SELECT pm.season, pm.kode, t.nama AS tim, pm.pemain, pm.pemain_id,
       pm.role, ro.urutan AS role_urutan,
       pm.main, pm.kill, pm.mati, pm.assist,
       pm.kill_rata, pm.mati_rata, pm.assist_rata,
       pm.kda, pm.partisipasi
FROM pemain_musim pm
JOIN tim t       ON t.kode = pm.kode
LEFT JOIN role ro ON ro.role = pm.role;
