-- Evolusi meta: hero yang paling dihormati tiap musim.
-- "Presence" = berapa persen game hero itu di-pick ATAU di-ban. Angka ini lebih
-- jujur daripada jumlah pick: hero yang terlalu kuat justru jarang ter-pick
-- karena selalu dibuang lebih dulu.
SELECT season,
       hero,
       picks,
       bans,
       presence || '%' AS presence,
       win_rate
FROM v_hero_meta
WHERE presence IS NOT NULL
  AND presence = (SELECT MAX(x.presence) FROM v_hero_meta x WHERE x.season = v_hero_meta.season)
ORDER BY season;
