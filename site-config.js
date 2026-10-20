/* Public website configuration. Add released apps to apps; never place secrets here. */
window.MITOM_CONFIG = {
  brand: 'MITOM',
  canonicalUrl: 'https://mitomyhy.github.io/',
  githubUrl: 'https://github.com/Mitomyhy',
  repositoryUrl: 'https://github.com/Mitomyhy/uoy-photoshop-panel',
  releaseUrl: 'https://github.com/Mitomyhy/Mitomyhy.github.io/releases/tag/v2026.10.21.2',
  launcherDownloadUrl: 'https://github.com/Mitomyhy/Mitomyhy.github.io/releases/download/v2026.10.21.2/MITOM-Launcher-Setup-2026.10.21.2.exe',
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
    version: '2026.10.20.3',
    detail: {
      en: 'Photoshop Panel brings design tools and order processing into Adobe Photoshop. Install, check and update the panel with MITOM Launcher.',
      vi: 'Photoshop Panel tập hợp công cụ hỗ trợ thiết kế và xử lý đơn hàng trong Adobe Photoshop. Cài đặt, kiểm tra và cập nhật panel qua MITOM Launcher.'
    }
  }]
};
