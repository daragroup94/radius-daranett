# 🚀 RADIUS Manager & FreeRADIUS Dashboard

Sistem manajemen billing, otentikasi, dan pemantauan terpusat untuk ISP skala kecil hingga menengah. Proyek ini menggabungkan **FreeRADIUS 3.0** (dengan backend database **PostgreSQL**), caching **Redis**, web panel **Flask Dashboard**, dan load balancer **Traefik** dengan SSL otomatis dari Let's Encrypt (Cloudflare).

Layanan ini mendukung multi-protokol otentikasi:
* **PPPoE** (Otentikasi PPPoE standard)
* **IPoE** (Otentikasi transparan berbasis MAC Address untuk DHCP Server)
* **Hotspot** (Manajemen voucher & lock MAC)
* **VPN** (Otentikasi user VPN seperti L2TP/SSTP/Wireguard)

---

## 🛠️ Persyaratan Awal (VPS Fresh)

Sebelum melakukan instalasi, pastikan VPS Anda (disarankan Ubuntu 22.04 LTS / 24.04 LTS) sudah terpasang Docker dan Docker Compose.

### 1. Install Docker & Docker Compose
Jalankan perintah ini di VPS fresh Anda:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose Plugin
sudo apt install -y docker-compose-plugin
```

### 2. Buat Network Eksternal Docker
Proyek ini menggunakan jaringan eksternal bernama `my-net` untuk interkoneksi container. Anda **wajib** membuatnya secara manual sebelum menjalankan container:
```bash
docker network create my-net
```

---

## 🚀 Langkah Instalasi

1. **Clone repositori ini:**
   ```bash
   git clone https://github.com/daranett/project-radius.git
   cd project-radius
   ```

2. **Setup Environment Variables:**
   Salin file template `.env.example` menjadi `.env` dan edit isinya:
   ```bash
   cp .env.example .env
   nano .env
   ```
   *Sesuaikan password database, token bot Telegram, email pgAdmin, dan domain Traefik Anda.*

3. **Jalankan Project:**
   Build dan jalankan seluruh container menggunakan Docker Compose:
   ```bash
   docker compose up -d --build
   ```

4. **Verifikasi Status:**
   Pastikan seluruh layanan (Database, Redis, RADIUS, Dashboard, pgAdmin) berjalan normal:
   ```bash
   docker compose ps
   ```

---

## 🧭 Akses Web Dashboard & PgAdmin

* **Web Dashboard:** `https://radius.access.daragroup.cloud` (atau port `5000` internal `http://your-vps-ip:5000`)
* **pgAdmin (Manajemen DB):** `https://pgadmin.access.daragroup.cloud`

---

## ⚙️ Integrasi MikroTik IPoE (MAC Authentication)

Untuk client IPoE (DHCP MAC Authentication), konfigurasi dibuat dinamis & aman. Nama dynamic queue di MikroTik otomatis diubah menjadi **Static Queue** dengan nama kustom sesuai dengan **Nama Pelanggan** yang didaftarkan di dashboard web (contoh: `IPoE-zte-lama`).

### 1. Pengaturan di MikroTik (WinBox)
* Masuk ke **IP > DHCP Server**.
* Klik dua kali pada server DHCP yang ingin digunakan untuk IPoE (contoh: `dhcp2`).
* Pada tab **DHCP**, centang opsi **Use Radius** menjadi aktif (`yes`).
* Klik kolom **Lease Script** di bagian bawah, lalu masukkan script final di bawah ini:

### 📋 DHCP Lease Script (RouterOS v7)
```routeros
:if ($leaseBound = 1) do={
    :delay 2s; # Tunggu lease stabil
    :do {
        # Mengambil info gabungan (Nama;RateLimit) secara internal/eksternal dari API Dashboard
        :local res [/tool fetch url="https://radius.access.daragroup.cloud/api/ipoe/get-name/$leaseActMAC" check-certificate=no as-value output=user]
        :local data ($res->"data")
        
        # Cari posisi titik koma (;) untuk memisah data
        :local pos [:find $data ";"]
        :if ($pos > 0) do={
            :local name [:pick $data 0 $pos]
            :local rate [:pick $data ($pos+1) [:len $data]]
            
            # Hapus queue bawaan (jika ada) agar tidak bentrok
            /queue simple remove [find target="$leaseActIP/32"]
            
            # Jika user memiliki rate limit di dashboard, buat static queue kustom
            :if ([:len $rate] > 0) do={
                /queue simple add name="IPoE-$name" target="$leaseActIP/32" max-limit=$rate parent=GLOBAL
            }
        }
    } on-error={
        :log error "Gagal memproses limitasi IPoE dari RADIUS Dashboard untuk MAC: $leaseActMAC"
    }
} else={
    # Saat client terputus (leaseBound = 0), hapus static queue secara otomatis
    /queue simple remove [find target="$leaseActIP/32"]
}
```

---

## ☕ Dukungan & Donasi
Jika proyek ini membantu usaha Anda, dukung pengembang melalui donasi:
* **DANA:** `081211081663`
