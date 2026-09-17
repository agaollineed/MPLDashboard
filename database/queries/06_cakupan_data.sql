-- Laporan cakupan: kolom mana yang terisi untuk musim mana.
-- Jalankan ini lebih dulu sebelum menganalisis apa pun — beberapa kolom hanya
-- ada untuk sebagian musim, dan itu batas SUMBERNYA, bukan data yang hilang.
SELECT m.season,
       m.sumber,
       (SELECT COUNT(*) FROM laga  l WHERE l.season = m.season)              AS laga,
       (SELECT COUNT(*) FROM game  g JOIN laga l ON l.laga_id = g.laga_id
        WHERE l.season = m.season)                                           AS game,
       (SELECT COUNT(*) FROM pick_ban pb JOIN laga l ON l.laga_id = pb.laga_id
        WHERE l.season = m.season)                                           AS pick_ban,
       (SELECT COUNT(*) FROM hero_relasi hr WHERE hr.season = m.season)      AS hero_relasi,
       (SELECT COUNT(*) FROM pemain_musim pm WHERE pm.season = m.season)     AS statistik_kda,
       (SELECT COUNT(*) FROM tim_musim tm
        WHERE tm.season = m.season AND tm.gold IS NOT NULL)                  AS statistik_objektif,
       (SELECT COUNT(*) FROM tim_musim tm
        WHERE tm.season = m.season AND tm.lolos_playoff IS NOT NULL)         AS playoff_dinilai,
       (SELECT COUNT(*) FROM award a WHERE a.season = m.season)              AS award
FROM musim m
ORDER BY m.season;
