# Perbaikan Bug: Line Kabel Menarik Garis Lurus Otomatis

## Masalah
- Sebelumnya, sistem akan otomatis menggambar garis lurus dari ODP ke customer jika `coordinates` kosong/NULL
- User tidak bisa tarik kabel manual sesuai rute jalan karena ada aturan lama yang menggambar topologi otomatis
- Bug terjadi di bagian `renderCables()` dan `updateTopology()` yang default ke straight line

## Solusi yang Diterapkan

### 1. Frontend (network-map.html)
- **renderCables()**: Hanya menampilkan kabel yang sudah memiliki coordinates manual (array tidak kosong)
  - Skip kabel dengan `coordinates === null` atau `coordinates.length === 0`
  - Hapus logika fallback ke straight line
  
- **saveCablePath()**: Tambah validasi path tidak boleh kosong
  - User harus klik minimal 1 titik di peta untuk membuat rute lekukan
  - Error message jika path masih kosong
  
- **saveInfraCablePath()**: Validasi yang sama untuk koneksi infrastruktur (OLT-ODC-ODP)

### 2. Backend (network_map.py)
- **api_add_cable()**: Tambah validasi coordinates wajib ada dan tidak boleh array kosong
  ```python
  if not coordinates or not isinstance(coordinates, list) or len(coordinates) == 0:
      return error
  ```
  
- **api_add_infra_connection()**: Validasi yang sama untuk infrastructure connections

### 3. Database Cleanup
- Script SQL untuk membersihkan data lama: `cleanup_empty_cables.sql`
- Hapus semua cable_connections dan infrastructure_connections dengan coordinates kosong

## Cara Menggunakan Setelah Perbaikan

### Untuk Tarik Kabel Customer (ODP ke Rumah):
1. Klik tombol "Tarik Kabel dari ODP" di popup customer
2. Pilih ODP dari daftar atau klik marker ODP hijau di peta
3. **Klik beberapa titik di peta** mengikuti rute jalan dari ODP ke rumah customer
4. Klik "Simpan Rute" atau langsung klik marker rumah customer untuk auto-snap

### Untuk Tarik Kabel Infrastruktur (OLT-ODC-ODP):
1. Klik popup ODP/ODC, pilih "Tarik Kabel"
2. Pilih target dari daftar atau klik marker target di peta
3. **Klik beberapa titik di peta** mengikuti rute jalan
4. Klik "Simpan Rute" atau klik marker target untuk auto-snap

## Hasil
- ✅ Tidak ada lagi garis lurus otomatis
- ✅ Semua kabel harus ditarik manual sesuai rute jalan
- ✅ Rute kabel tersimpan di database sebagai array coordinates
- ✅ Line mengikuti jalur yang sudah ditarik user

## Cara Jalankan Cleanup Database
```bash
# Masuk ke container database
docker exec -it project-radius-db-1 bash

# Jalankan script cleanup
psql -U radius -d radius -f /cleanup_empty_cables.sql

# Atau langsung dari host
docker exec -i project-radius-db-1 psql -U radius -d radius < cleanup_empty_cables.sql
```

## Testing
1. Buat kabel baru dengan cara tarik manual di peta
2. Pastikan tidak ada garis lurus otomatis yang muncul
3. Coba simpan tanpa klik titik di peta → harus muncul error
4. Reload halaman → kabel manual tetap tersimpan dan ditampilkan

---
Dibuat: 2026-06-05
Oleh: Kiro AI Assistant
