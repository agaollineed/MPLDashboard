-- Pasangan hero terbaik dan lawan terberat pada satu musim.
--
-- Dua jenis relasi ini punya aturan simetri yang BERBEDA:
--   rekan : (A,B) dan (B,A) identik        -- menang bersama, kalah bersama
--   lawan : (A,B) dan (B,A) berkebalikan   -- menang satu = kalah yang lain
-- Karena itu keduanya tidak boleh dicampur dalam satu agregat.
SELECT jenis,
       hero,
       hero_lain,
       jumlah,
       menang,
       kalah,
       win_rate
FROM hero_relasi
WHERE season = 17
  AND jumlah >= 20
  AND (jenis, win_rate) IN (
      SELECT jenis, MAX(win_rate) FROM hero_relasi
      WHERE season = 17 AND jumlah >= 20 GROUP BY jenis, hero)
ORDER BY jenis, win_rate DESC
LIMIT 20;
