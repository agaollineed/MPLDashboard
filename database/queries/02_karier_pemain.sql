-- Pemain dengan karier terpanjang, beserta capaiannya.
-- `gelar` datang dari keanggotaan regu juara; `award` dari penghargaan
-- perorangan. Keduanya sengaja dihitung dari sumber yang berbeda.
SELECT nama,
       negara,
       musim,
       'S' || musim_awal || '-S' || musim_akhir AS rentang,
       jumlah_tim,
       gelar,
       award,
       mvp_laga
FROM v_karier_pemain
ORDER BY musim DESC, gelar DESC, award DESC
LIMIT 20;
