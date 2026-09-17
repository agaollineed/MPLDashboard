-- Rekam jejak tiap tim sepanjang sembilan musim.
-- Perhatikan pembagi kolom terakhir: musim yang playoff-nya belum dimainkan
-- tidak boleh dihitung sebagai "gagal lolos", jadi yang dihitung hanya musim
-- yang lolos_playoff-nya tidak NULL.
SELECT t.nama                                          AS tim,
       COUNT(*)                                        AS musim,
       SUM(tm.menang)                                  AS menang,
       SUM(tm.kalah)                                   AS kalah,
       ROUND(100.0 * SUM(tm.menang) / SUM(tm.main), 1) AS win_rate,
       MIN(tm.peringkat)                               AS peringkat_terbaik,
       SUM(tm.lolos_playoff)                           AS lolos_playoff,
       COUNT(tm.lolos_playoff)                         AS musim_ada_playoff,
       SUM(CASE WHEN tm.hasil_playoff = 'Champion' THEN 1 ELSE 0 END) AS gelar
FROM tim_musim tm
JOIN tim t ON t.kode = tm.kode
GROUP BY t.kode, t.nama
ORDER BY gelar DESC, win_rate DESC;
