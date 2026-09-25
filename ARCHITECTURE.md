# L3B Architecture Record — Multi-Agent MCP + A2A

Tài liệu thiết kế hệ thống giải quyết khiếu nại sàn TMĐT Olist dành cho cuộc thi L3B.

---

## 1. System Overview

Hệ thống hoạt động theo mô hình **Supervisor / Router Điều Phối & 3 Worker Chuyên Sâu**:

```text
[Input Case] 
     │
     ▼
[Supervisor / Router Agent] ── (Extract/Resolve Entity & Issue)
     │
     ├──► Task 1 ──► [Policy Worker]    ──► Tra cứu điều khoản Olist, check hạn 7/30 ngày
     │
     ├──► Task 2 ──► [Logistics Worker] ──► Đối soát bảng orders/shipments, check lỗi giao trễ
     │
     └──► Task 3 ──► [Financial Worker] ──► Truy vấn order_payments, tính tiền hoàn chuẩn xác
     │
     ▼
[Synthesis & Conflict Resolver] ──► Phân giải mâu thuẫn khách vs DB, chốt root cause
     │
     ▼
[Verifier] ──► Kiểm tra Invariants & Schema JSON
     │
     ▼
[Final Output] + [trace.jsonl]
```

Toàn bộ các Worker giao tiếp với Olist Database thông qua **MCP Gateway** và phát các sự kiện vòng đời chuẩn chỉ qua **TraceWriter**.

---

## 2. Agent Ownership

| Actor | Input | Trách nhiệm | Tool permission | Output / Handoff |
| :--- | :--- | :--- | :--- | :--- |
| **Supervisor / Router** | `case` JSON (claim text, candidates) | Bóc tách thực thể, phân loại khiếu nại, điều phối task cho 3 Worker, tổng hợp phán quyết cuối cùng | Không gọi DB trực tiếp | `order_id`, `primary_issue`, task assignments |
| **Policy Worker** | `product_category_name`, `order_date`, `claim_date` | Tra cứu quy chế Olist, kiểm tra thời hiệu (7 ngày đổi trả, 30 ngày bảo hành kỹ thuật), trích dẫn điều khoản | `get_policy_clause`, `get_category_rules` | `policy_verdict`, `applicable_clauses`, `evidence_refs` |
| **Logistics Worker** | `order_id`, candidate orders | Truy vấn bảng `orders`, `order_items`, `shipments`; so khớp ngày giao thực tế vs ngày hẹn; phân định lỗi Shipper vs Seller | `get_order`, `get_order_items`, `get_shipment_summary` | `shipment_analysis` (`verdict`, `late_seller_ids`, `timeline_complete`), `affected_entities`, `evidence_refs` |
| **Financial Worker** | `order_id` | Truy vấn bảng `order_payments`; áp dụng Deterministic Logic tính `captured`, `refunded`, `refundable`, `recommended_refund_brl` | `get_order_payments`, `get_refund_history` | `payment_analysis`, `financial_resolution` |
| **Verifier** | Aggregated payload | Kiểm tra toàn vẹn dữ liệu (Schema, tổng tiền không âm, `evidence_refs` hợp lệ, không vượt quá max refundable) | Không dùng tool | `validated_output` JSON |

---

## 3. Entity Resolution & A2A Protocol

1. **Candidate Resolution Strategy:**
   - Trường hợp case không có sẵn `order_id`: Supervisor quét danh sách `candidate_orders` dựa trên so khớp `customer_id`, khoảng thời gian đặt hàng và giá trị đơn.
   - Ngưỡng tin cậy (Confidence Threshold): $\ge 0.85$ coi là `resolved`; dưới ngưỡng chuyển sang `ambiguous` hoặc `not_found`.
   - Các candidates không thỏa mãn được đưa vào danh sách `rejected_candidates`.

2. **A2A Message Passing & Correlation:**
   - Mỗi task gửi tới Worker được gắn kèm `case_id` và context thực thể đã resolve.
   - Sự kiện chuyển giao công việc được ghi nhận qua Trace Event:
     - `task_assigned`: Supervisor giao việc cho Worker.
     - `tool_result_consumed`: Worker nhận dữ liệu và `evidence_ref` từ MCP.
     - `handoff`: Worker trả kết quả phân tích về cho Supervisor.

---

## 4. Evidence & Conflict Lifecycle

1. **Quản lý Evidence (`evidence_ref`):**
   - Mọi truy vấn qua `gateway.call()` đều trích xuất `evidence_ref` duy nhất do MCP Server cấp.
   - Tuyệt đối không tự sinh hoặc sửa đổi `evidence_ref`.
   - `evidence_refs` được tích lũy từ tất cả các Worker và nhúng vào `evidence_refs` của output cuối cùng.

2. **Xử lý Mâu Thuẫn Dữ Liệu (`data_conflicts`):**
   - So sánh giữa tuyên bố của khách hàng (`customer_claim`) và bản ghi hệ thống (`olist_system_record`).
   - Ưu tiên nguồn tin: **Log vận chuyển / Cổng thanh toán hệ thống > Lời khai khách hàng**.
   - Nếu có sai lệch (ví dụ: khách bảo chưa nhận nhưng shipper đã có chữ ký xác nhận giao đúng hạn), ghi nhận vào danh sách `data_conflicts` với resolution code tương ứng.

---

## 5. Failure and Efficiency Policy

| Failure Scenario | Retry Budget | Fallback Strategy | Trace Event / Code |
| :--- | :---: | :--- | :--- |
| **MCP Timeout / Error** | Tối đa 2 lần | Trả về `insufficient_evidence`, giữ nguyên dữ liệu đã có | `tool_call_failed` |
| **Entity Not Found / Ambiguous** | 0 lần (không quét bừa) | Gán `status: "ambiguous"`, `case_status: "needs_investigation"` | `entity_unresolved` |
| **Source Conflict** | 0 lần | Đưa vào mảng `data_conflicts`, chọn System Record làm nguồn chính | `conflict_detected` |
| **Invalid Worker Output** | 1 lần | Supervisor sử dụng heuristic an toàn (`no_action` / `needs_investigation`) | `worker_fallback` |

* **Chiến lược tối ưu Efficiency:**
  - **In-memory Caching theo case:** Lưu cache các kết quả gọi MCP trong cùng 1 case để không gọi lặp tool.
  - **Chỉ gọi tool cần thiết:** Không gọi bừa toàn bộ bảng dữ liệu nếu vụ việc không liên quan (ví dụ: khiếu nại về giao trễ thì không cần gọi sâu vào lịch sử voucher nếu không yêu cầu hoàn tiền).

---

## 6. Verification Invariants (Quy Tắc Kiểm Định Bắt Buộc)

Trước khi finalize output, **Verifier** kiểm tra các điều kiện bất biến (Invariants):
1. **Schema Invariant:** Output khớp 100% với `contracts/schemas/l3b-output-v2.schema.json`.
2. **Financial Invariant:**
   - $\text{captured\_total\_brl} \ge \text{refunded\_total\_brl} + \text{refundable\_total\_brl}$
   - $\text{recommended\_refund\_brl} \le \text{refundable\_total\_brl}$
   - Tổng tiền trong `refund_lines` phải bằng chính xác `recommended_refund_brl`.
3. **Evidence Invariant:** Tất cả các `evidence_refs` trong output phải là các mã đã nhận từ MCP và đã được emit trace `tool_result_consumed`.
4. **Consistency Invariant:** Nếu `case_status == "no_action"` thì `recommended_refund_brl` phải bằng 0.

---

## 7. Reproducibility & Environment

* **Python Version:** $\ge 3.11$
* **Deterministic Logic:** Các phép tính toán tiền tệ và kiểm tra hạn thời gian được code bằng logic toán học thuần túy (không phụ thuộc tính ngẫu nhiên của LLM).
* **Dependencies:** Được cố định trong `pyproject.toml`.
* **Execution Command:**
  ```bash
  day09 run
  day09 validate
  day09 package --output dist/submission.zip
  ```
