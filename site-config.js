/* Public website configuration. Add released apps to apps; never place secrets here. */
window.MITOM_CONFIG = {
  brand: 'MITOM',
  canonicalUrl: 'https://mitomyhy.github.io/',
  githubUrl: 'https://github.com/Mitomyhy',
  repositoryUrl: 'https://github.com/Mitomyhy/uoy-photoshop-panel',
  releaseUrl: "https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/mitom-launcher/2026.10.25.7",
  launcherVersion: "2026.10.25.7",
  launcherDownloadUrl: "https://mitomyhy.github.io/releases/mitom-launcher/2026.10.25.7/MITOM-Launcher-Setup-2026.10.25.7.exe",
  launcherProtocol: 'uoy://',
  apps: [{
    id: 'photoshop-panel',
    name: 'Photoshop Panel',
    category: { en: 'Photoshop extension', vi: 'Tiện ích Photoshop' },
    platform: 'WINDOWS + ADOBE PHOTOSHOP',
    icon: 'favicon.ico',
    status: 'available',
    statusLabel: { en: 'AVAILABLE VIA LAUNCHER', vi: 'SẴN SÀNG QUA LAUNCHER' },
    summary: {
      en: '3D tools, order management, Lark Base and creative connections, inside your Photoshop workspace.',
      vi: 'Công cụ 3D, quản lý đơn hàng, Lark Base và các kết nối sáng tạo trong không gian Photoshop.'
    },
    version: "2026.10.25.1",
    detail: {
      en: 'Photoshop Panel brings design tools and order processing into Adobe Photoshop. Install, check and update the panel with MITOM Launcher.',
      vi: 'Photoshop Panel tập hợp công cụ hỗ trợ thiết kế và xử lý đơn hàng trong Adobe Photoshop. Cài đặt, kiểm tra và cập nhật panel qua MITOM Launcher.'
    }
  }, {
    id: 'vector-migic',
    name: 'Vector Migic',
    category: { en: 'Raster to vector', vi: 'Chuyển ảnh thành vector' },
    platform: 'WINDOWS · OFFLINE CPU',
    icon: 'vector-migic.png',
    githubUrl: "https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/vector-migic/1.0.4",
    status: 'available',
    statusLabel: { en: 'AVAILABLE VIA LAUNCHER', vi: 'SẴN SÀNG QUA LAUNCHER' },
    summary: {
      en: 'Trace PNG, JPG, WebP, BMP and TIFF into editable AI, SVG, PDF and EPS vector paths on your computer.',
      vi: 'Chuyển PNG, JPG, WebP, BMP và TIFF thành đường vector AI, SVG, PDF và EPS ngay trên máy của bạn.'
    },
    version: "1.0.4",
    detail: {
      en: 'Convert raster images into vector paths with color or black-and-white tracing, logo, illustration and photo presets, transparency controls and batch export. Runs locally on CPU with no cloud account or credits.',
      vi: 'Chuyển ảnh thành đường vector bằng chế độ màu hoặc đen trắng, preset logo, minh họa và ảnh chụp, tùy chọn nền trong suốt và xuất hàng loạt. Chạy local bằng CPU, không cần tài khoản đám mây hay credit.'
    },
    note: {
      en: 'Runs independently on Windows. Adobe Photoshop and a dedicated GPU are not required.',
      vi: 'Chạy độc lập trên Windows. Không cần Adobe Photoshop hay card đồ họa rời.'
    }
  }, {
    id: 'mifont',
    name: 'MiFont',
    category: { en: 'Images to fonts', vi: 'Tạo font từ ảnh' },
    platform: 'WINDOWS · LOCAL CPU',
    icon: 'mifont.png',
    githubUrl: "https://github.com/Mitomyhy/Mitomyhy.github.io/tree/main/releases/mifont/1.0.2",
    status: 'available',
    statusLabel: { en: 'AVAILABLE VIA LAUNCHER', vi: 'SẴN SÀNG QUA LAUNCHER' },
    summary: {
      en: 'Upload images, name your font and download TTF and OTF together with one button.',
      vi: 'Tải ảnh lên, đặt tên font và tải cả TTF lẫn OTF bằng một nút.'
    },
    version: "1.0.2",
    detail: {
      en: 'Automatic letter detection, licensed open-font comparison, missing-character completion and one font ZIP. Keep only the ink threshold control; generation runs locally on CPU.',
      vi: 'Tự nhận diện chữ, đối chiếu font mở có giấy phép, bổ sung ký tự thiếu và xuất một ZIP chứa cả hai font. Chỉ cần chỉnh ngưỡng mực; xử lý chạy trên CPU.'
    },
    note: {
      en: 'Latin ASCII95. Images stay on your computer. Online lookup uses public open fonts; missing letters are estimates rather than guaranteed recovery of the original font.',
      vi: 'Gồm95 ký tự ASCII Latin. Ảnh luôn ở máy của bạn. Tìm trực tuyến từ font mở; chữ chưa có là ước lượng, không bảo đảm khôi phục đúng font gốc.'
    }
  }]
};

