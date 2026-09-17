-- Prioritas draft: hero apa yang paling sering diambil atau dibuang PALING AWAL.
-- Urutan 1 adalah pick/ban pertama tim itu di game tersebut, jadi ini mengukur
-- hero mana yang dianggap paling menentukan — bukan sekadar paling sering muncul.
SELECT season,
       jenis,
       hero,
       COUNT(*) AS kali_pertama
FROM v_draft
WHERE urutan = 1
GROUP BY season, jenis, hero
HAVING kali_pertama >= 10
ORDER BY season DESC, jenis, kali_pertama DESC;
