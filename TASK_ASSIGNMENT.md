# BẢNG PHÂN CHIA NHIỆM VỤ NHÓM 4 NGƯỜI
## DAY 09: MULTI-AGENT ARCHITECTURE — HỆ THỐNG XỬ LÝ KHIẾU NẠI SÀN TMĐT OLIST

> **Mục tiêu:** Hiện thực hóa Router Điều Phối & 3 Worker Chuyên Sâu, kết nối dữ liệu Olist qua MCP và tạo chuỗi đối soát bằng chứng A2A (Agent-to-Agent).

---

## 👥 TỔNG QUAN PHÂN VAI TRÒ (4 THÀNH VIÊN)

| Thành viên | Vai trò (Role) | Chuyên môn cốt lõi | File / Module phụ trách |
| :--- | :--- | :--- | :--- |
| **Ngô Thế Việt** | 👑 **Supervisor / Router Agent** | Điều phối thông minh, trích xuất thực thể, tổng hợp phán quyết & quản lý Trace | `workflow.py`, `coordinator.py`, `trace.py` |
| **Nguyễn Quang Đạo** | 📜 **Policy Worker** | Tra cứu quy chế Olist, kiểm tra thời hiệu, trích dẫn điều khoản chính xác | `policy_worker.py`, MCP Policy tools |
| **Nguyễn Văn Giáp** | 🚚 **Logistics Worker** | Đối soát vận đơn (`orders`, `order_items`), phân định lỗi Shipper vs Seller | `logistics_worker.py`, MCP Shipment tools |
| **Cao Đức Hiệp** | 💰 **Financial Worker** | Đối soát thanh toán (`order_payments`), tính toán số tiền hoàn (Deterministic Logic) | `financial_worker.py`, `verifier.py`, MCP Payment tools |

---

## 📌 CHI TIẾT NHIỆM VỤ TỪNG THÀNH VIÊN

### 👑 1. Ngô Thế Việt: SUPERVISOR / ROUTER AGENT (Team Lead & Điều Phối)

* **Vai trò:** Trung tâm điều phối, tiếp nhận case khiếu nại, phân chia luồng xử lý và đưa ra phán quyết cuối cùng.
* **Nhiệm vụ chi tiết:**
  1. **Phân tích ngữ nghĩa đơn khiếu nại (NLP/Semantic Parsing):**
     - Đọc nội dung khiếu nại từ input (`inputs/L3B_CASE_*.json`).
     - Trích xuất `order_id` (hoặc phối hợp resolve entity nếu chỉ có candidate).
     - Xác định loại tranh chấp chính (`primary_issue`).
  2. **Ra lệnh điều phối thông minh (Smart Orchestration):**
     - Xác định thứ tự gọi các Worker (Worker nào xử lý trước, Worker nào xử lý sau).
     - Gửi yêu cầu (task delegation) và nhận dữ liệu bàn giao (`handoff`) giữa các Worker.
  3. **Tổng hợp bằng chứng & Phán quyết cuối cùng:**
     - Thu thập kết quả đối soát từ 3 Worker (Policy, Logistics, Financial).
     - Đưa ra kết luận vụ việc (`assessment.case_status`, `root_cause_analysis`, `resolution_actions`).
  4. **Quản lý Trace & Pipeline:**
     - Ghi nhận đầy đủ chuỗi sự kiện Trace (`case_received`, `task_assigned`, `handoff`, `case_finalized`) vào `traces/trace.jsonl`.
     - Phụ trách chạy lệnh pipeline (`day09 run`, `day09 validate`) và đóng gói nộp bài (`day09 package`).

---

### 📜 2. Nguyễn Quang Đạo: POLICY WORKER (Quy Chế Sàn & Pháp Lý)

* **Vai trò:** Chuyên gia pháp lý và quy chế sàn, đảm bảo mọi phán quyết đều có căn cứ điều khoản rõ ràng.
* **Nhiệm vụ chi tiết:**
  1. **Tra cứu điều khoản quy định Olist:**
     - Gọi MCP tool để lấy quy chế tương ứng với từng ngành hàng của sản phẩm trong đơn khiếu nại.
  2. **Kiểm tra thời hiệu khiếu nại:**
     - Kiểm tra chính sách: **7 ngày đổi trả** (đối với trả hàng/hoàn tiền thông thường).
     - Kiểm tra chính sách: **30 ngày bảo hành** (đối với lỗi kỹ thuật/hư hỏng).
     - So sánh mốc thời gian khách khiếu nại với thời điểm nhận hàng để xác định khiếu nại còn hạn hay quá hạn.
  3. **Trích dẫn bằng chứng chuẩn xác:**
     - Trích dẫn chính xác số điều khoản (Clause/Policy Ref), **tuyệt đối không bịa đặt quy định**.
     - Lưu lại `evidence_ref` của Policy từ MCP để đưa vào `evidence_refs` của output.

---

### 🚚 3. NGuyễn Văn Giáp: LOGISTICS WORKER (Vận Đơn & Giao Nhận)

* **Vai trò:** Chuyên gia điều tra chuỗi cung ứng, đối soát hành trình đơn hàng và xác định trách nhiệm chậm trễ.
* **Nhiệm vụ chi tiết:**
  1. **Truy vấn dữ liệu vận chuyển trong Olist DB:**
     - Gọi các MCP tool để lấy dữ liệu từ các bảng `orders`, `order_items`, `shipments`.
  2. **Đối soát mốc thời gian (Timeline Reconciliation):**
     - So sánh **ngày giao thực tế** (`order_delivered_customer_date`) với **ngày hẹn dự kiến** (`order_estimated_delivery_date`).
     - Kiểm tra mốc bàn giao hàng của người bán (`shipping_limit_date`).
  3. **Phân định lỗi chậm trễ (Fault Attribution):**
     - Đưa ra `shipment_analysis.verdict`:
       - `seller_delay`: Người bán giao hàng trễ cho shipper $\rightarrow$ đưa vào `late_seller_ids`.
       - `logistics_delay`: Người bán gửi đúng hạn nhưng đơn vị vận chuyển giao trễ.
       - `lost` / `returned` / `on_time`.
  4. **Bắn Trace Event:**
     - Emit trace `tool_result_consumed` cho mỗi lần lấy dữ liệu vận đơn thành công.

---

### 💰 4. Cao Đức Hiệp: FINANCIAL WORKER (Kiểm Soát Tiền & Verifier)

* **Vai trò:** Chuyên gia đối soát tài chính và kiểm định chất lượng output trước khi xuất kết quả.
* **Nhiệm vụ chi tiết:**
  1. **Truy vấn bảng thanh toán `order_payments`:**
     - Lấy thông tin thanh toán qua MCP: Giá hàng (`price`), phí vận chuyển (`freight_value`), phương thức thanh toán, voucher/coupon (nếu có).
  2. **Tính toán số tiền hoàn (Deterministic Logic):**
     - Áp dụng công thức toán học chặt chẽ, không để LLM đoán mò số tiền.
     - Tính chính xác:
       - `captured_total_brl`: Tổng tiền sàn đã thu.
       - `refunded_total_brl`: Số tiền đã hoàn trước đó.
       - `refundable_total_brl`: Số tiền tối đa còn có thể hoàn.
       - `recommended_refund_brl` & chi tiết từng dòng `refund_lines` (kèm `reason_code`, `amount_brl`).
  3. **Kiểm tra Verifier & JSON Schema Invariants:**
     - Kiểm tra đầu ra khớp 100% với schema `contracts/schemas/l3b-output-v2.schema.json`.
     - Kiểm tra tính nhất quán logic: Số tiền đề xuất hoàn không được vượt quá số tiền tối đa có thể hoàn; các `evidence_refs` phải tồn tại và hợp lệ.

---

## 🔄 SƠ ĐỒ PHỐI HỢP CÔNG VIỆC (A2A WORKFLOW)

```text
                        [INPUT CASE KHIẾU NẠI]
                                  │
                                  ▼
                     👑 THÀNH VIÊN 1 (Supervisor)
                 Phân tích ngữ nghĩa, bóc tách order_id
                                  │
      ┌───────────────────────────┼───────────────────────────┐
      ▼                           ▼                           ▼
📜 THÀNH VIÊN 2             🚚 THÀNH VIÊN 3             💰 THÀNH VIÊN 4
(Policy Worker)            (Logistics Worker)          (Financial Worker)
 Tra cứu quy chế Olist     Đối soát hành trình         Truy vấn order_payments
 Kiểm tra hạn 7/30 ngày    Xác định Shipper vs Seller  Tính toán số tiền hoàn
 Trích dẫn điều khoản      Phân định lỗi giao trễ      Áp dụng Deterministic Logic
      │                           │                           │
      └───────────────────────────┼───────────────────────────┘
                                  │
                                  ▼
                     👑 THÀNH VIÊN 1 & 4 (Synthesis & Verifier)
                 Tổng hợp phán quyết cuối cùng & Kiểm tra Schema
                                  │
                                  ▼
                   [OUTPUT JSON & TRACE.JSONL]
```

---

## 🎯 TIÊU CHÍ ĐẠT CHECKPOINT (GATE 3 PASS)
- [ ] Test case thực tế đầu tiên chạy mượt mà từ đầu đến cuối (`day09 run`).
- [ ] Cả 3 Worker trả về đầy đủ bằng chứng đối soát (`evidence_refs` hợp lệ từ MCP).
- [ ] Supervisor chốt phán quyết chính xác, tính toán tiền chuẩn xác và pass toàn bộ schema (`day09 validate`).
