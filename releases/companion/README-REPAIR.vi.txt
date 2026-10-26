MITOM — tiện ích Chrome và kiểm tra Base Companion

Tải riêng trên https://mitomyhy.github.io/#extension-downloads
Base Companion 1.0.27; Canva Companion 1.0.3; Photoroom Companion 1.0.6.
Các gói Canva/Photoroom được tách nguyên mã từ Panel 2026.10.25.1 đã phát hành, không kèm hồ sơ Chrome hoặc thiết lập máy.

CÀI MỚI
1. Giải nén ZIP vào thư mục cố định trên chính máy nhận.
2. Mở chrome://extensions, bật Chế độ dành cho nhà phát triển, chọn Tải tiện ích đã giải nén.
3. Chọn thư mục có manifest.json: Base là thư mục Chrome-Base-Companion bên trong gói; Canva/Photoroom là thư mục vừa giải nén, nơi thấy manifest.json.
4. Mỗi máy phải kết nối tiện ích với Panel/cầu nối đang chạy trên CHÍNH máy đó. Chép file tiện ích không chép được ghép nối Chrome sang máy khác. Không chép User Data, token hoặc hồ sơ trình duyệt từ máy khác.

BASE ĐANG CÓ NHƯNG CHROME BÁO LỖI
Gói Check-Repair không tăng phiên bản tiện ích và không tự sửa mọi lỗi Chrome. Nó kiểm tra file, mẫu URL trong manifest và phiên bản Chrome đã tìm thấy (yêu cầu tối thiểu120), không kiểm tra trực tiếp trạng thái Chrome đang mở.
Giải nén gói Check-Repair, chạy Check Companion.cmd. Chọn đúng thư mục Chrome đang nạp, ví dụ C:\Users\ADMIN\AppData\Local\UOY\Companion trên máy nhận. Mặc định chỉ kiểm tra; chỉ khi bạn xác nhận REPAIR mới khôi phục mã trong các trường hợp đã nhận diện an toàn. Mã không nhận diện sẽ bị từ chối, không ghi đè.
Nếu khôi phục, bản gốc được giữ trong thư mục .uoy-companion-repair-backups. Sau đó bấm Tải lại ở TIỆN ÍCH ĐANG CÓ, rồi tải lại tab Base một lần. Không bấm Xóa, Quên hay ghép lại chỉ vì cập nhật mã.
Nếu kết quả là current_valid nhưng Chrome vẫn báo lỗi thì file đã đúng, chưa xác định nguyên nhân Chrome trên máy nhận; không cần cài lại toàn bộ Panel. Có thể gửi báo cáo kiểm tra, không cần tự sửa JSON hay đoán mã lỗi.

AN TOÀN
Không đọc/ghi hồ sơ Chrome, ghép nối, cookie, token, không sửa chính sách Chrome/Windows, không tắt bảo vệ và không yêu cầu quyền quản trị. CMD chạy một tiến trình PowerShell riêng cho chính script đi kèm; tuỳ chọn ExecutionPolicy chỉ tồn tại trong tiến trình đó, không đổi chính sách máy và không vượt chính sách quản trị bắt buộc.
Các ZIP tải riêng và bộ khôi phục không thay đổi kênh cập nhật Launcher/Panel, không yêu cầu tải lại model AI.
