# L3B Architecture Record — Multi-Agent MCP + A2A

Tài liệu thiết kế kiến trúc hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử Olist (L3B).

---

## 1. System Overview & Architecture

Hệ thống hoạt động theo mô hình **Coordinator / Router Điều Phối & 3 Worker Chuyên Sâu**, giao tiếp theo giao thức A2A (Agent-to-Agent) có cấu trúc, phối hợp truy xuất cơ sở dữ liệu qua **MCP Gateway** với audit trail nghiêm ngặt và kiểm định bằng chứng tự động.

```text
Input (Case)
    │
    ▼
[Coordinator / Router] ── task_assigned ──► [Entity Resolver Agent] ── MCP: get_customer_history, get_order
    │                                                   │
    │◄────────────────── handoff ───────────────────────┘
    │
    ├── task_assigned ─────────────────────► [Policy Agent] ────────── MCP: get_policy
    │                                                   │
    │◄────────────────── handoff ───────────────────────┘
    │
    ├── task_assigned ─────────────────────► [Order & Logistics Specialist] ── MCP: get_order_items, get_shipment_summary
    │                                                   │
    │◄────────────────── handoff ───────────────────────┘
    │
    ├── task_assigned ─────────────────────► [Payment & Refund Specialist] ─── MCP: get_payment_timeline, get_refund_timeline
    │                                                   │
    │◄────────────────── handoff ───────────────────────┘
    │
    ├── internal analysis ─────────────────► [Conflict Resolver]
    │                                                   │
    │◄────────────────── conflicts ─────────────────────┘
    │
    ▼
[Synthesizer / Coordinator]
    │
    ▼
[Verifier Agent] ────────────────────────── verification_completed (7 Invariant Gates)
    │
    ▼
Output (Validated JSON: outputs/<case_id>.json) + Trace (traces/trace.jsonl)
```

Toàn bộ các Worker giao tiếp với Olist Database thông qua **MCP Gateway** (có caching & audit) và phát các sự kiện vòng đời chuẩn chỉ qua **TraceWriter**.

---

## 2. Agent Ownership & Team Task Division (Phân chia 4 thành viên)

Hệ thống phân công trách nhiệm rõ ràng cho 4 thành viên trong nhóm theo từng vai trò chuyên môn hóa:

| Actor / Agent | Thành viên phụ trách | Input | Trách nhiệm cốt lõi | Tool permission (MCP) | Output / Handoff |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Supervisor / Router** (`coordinator`) | **Ngô Thế Việt** | Raw case JSON | Khởi tạo phiên, điều phối luồng A2A DAG, phân giải thực thể `entity_resolver`, giải quyết mâu thuẫn dữ liệu `conflict_resolver`, tổng hợp phán quyết cuối cùng | Không gọi DB trực tiếp | `case_received`, `task_assigned`, `case_finalized`, output JSON |
| **Policy Worker** (`policy_agent`) | **Nguyễn Quang Đạo** | `policy_version`, `claims`, `category` | Tra cứu quy chế Olist (`get_policy`), ánh xạ khiếu nại vào điều khoản, xác định `primary_issue`, mức hoàn tiền và bên chịu trách nhiệm | `get_policy` | `primary_issue`, `policy_rule`, `policy_ref`, `decision_code` |
| **Logistics Worker** (`logistics_worker`) | **Nguyễn Văn Giáp** | `order_id`, candidate orders | Phân tích mặt hàng (`get_order_items`), đối soát timeline giao nhận (`get_shipment_summary`), phân định lỗi bên vận chuyển (`logistics_delay`) vs người bán (`seller_delay`) | `get_order_items`, `get_shipment_summary` | `shipment_analysis` (verdict, late_seller_ids, timeline_complete), `item_ids`, `seller_ids` |
| **Financial Worker & Verifier** (`financial_worker` / `verifier`) | **Cao Đức Hiệp** | `order_id`, payment ledger, draft output | Đối soát dòng tiền (`get_payment_timeline`, `get_refund_timeline`), tính captured & refundable amount, kiểm tra 7 Invariant gates trước khi phát hành | `get_payment_timeline`, `get_refund_timeline` | `payment_analysis`, `financial_resolution`, `verification_completed` event |

---

## 3. Entity Resolution & A2A Protocol

- **Xếp hạng & Loại trừ Candidate**:
  - Candidate có tiền tố dạng `candidate-` hoặc độ dài không đạt chuẩn UUID/hex 32 ký tự bị loại trực tiếp vào `rejected_candidates`.
  - Candidate trùng với `claimed_order_id` và tồn tại trong `get_customer_history` được ưu tiên chọn làm `resolved_order_id`.
  - Phân loại trạng thái phân giải:
    - `resolved`: Tìm thấy đúng 1 order hợp lệ (confidence = `1.0`).
    - `ambiguous`: Tồn tại nhiều candidate hợp lệ không phân biệt được.
    - `not_found`: Không candidate nào hợp lệ.
- **A2A Protocol & Correlation**:
  - Mọi sự kiện giao tiếp giữa các agent đều được gắn kèm `case_id` để tương quan chặt chẽ.
  - Phân công việc qua sự kiện `task_assigned` ghi rõ `actor="coordinator"` và `target=<specialist_agent>`.
  - Chuyển giao kết quả qua `handoff` từ specialist về coordinator.
  - Không truyền chuỗi suy luận nội bộ hay prompt vào trace, chỉ ghi nhận observable events.

---

## 4. Evidence & Conflict Lifecycle

- **Evidence Lifecycle**:
  - Mọi phản hồi từ MCP Gateway được validate thông qua schema `mcp-evidence-response-v1`.
  - Lưu giữ chính xác mã `evidence_ref` do server cấp (`^ev_[A-Za-z0-9_-]{20,96}$`), tuyệt đối không tự sinh hay sửa đổi.
  - Mỗi khi kết quả tool được sử dụng bởi một agent, hệ thống phát sự kiện `tool_result_consumed` với `actor`, `tool_name` và `evidence_refs`.
  - Toàn bộ `evidence_ref` hợp lệ được tổng hợp vào trường `evidence_refs` của output case tương ứng. Không dùng chéo bằng chứng giữa các case (Zero-leaking invariant).
- **Conflict Lifecycle & Source Precedence**:
  - Phát hiện xung đột thời gian giữa snapshot `get_order` và lịch sử khách hàng `get_customer_history` quanh thời điểm mở khiếu nại (`opened_at`).
  - Khi có xung đột về `order_purchase_timestamp` hoặc `order_status`, hệ thống tạo mục `data_conflicts` với `selected_source="get_customer_history"`, độ ưu tiên theo temporal proximity (`prefer_temporal_proximity` / `prefer_complaint_scoped_status`).
  - Dấu mốc bưu cục (`Official carrier logs`) được ưu tiên hơn lời khai khách hàng khi có tranh chấp giao nhận.
  - Sổ cái thanh toán (`Payment gateway ledger`) được ưu tiên hơn siêu dữ liệu đơn hàng khi có tranh chấp số tiền.

---

## 5. Failure and Efficiency Policy (Call Budget Optimization)

| Failure Scenario | Retry Budget | Fallback Strategy | Trace Event / Code |
| :--- | :---: | :--- | :--- |
| **MCP Timeout / Error** | 2 retries | Tự động thử lại hoặc dùng dữ liệu từ snapshot | `tool_fallback_used` / `tool_timeout` |
| **Entity Ambiguous / Not Found** | 0 retries | Lấy candidate hợp lệ đầu tiên, đánh dấu rejected | `entity_status_decided` / `entity_resolved` |
| **Source Conflict** | 0 retries | Áp dụng rule ưu tiên customer history gần mốc khiếu nại | `conflict_resolved` |
| **Invalid Specialist Result** | 1 retry | Sử dụng giá trị fallback an toàn | `specialist_fallback` |

- **Chiến lược tối ưu hóa Call Budget (5.4 calls/case)**:
  - Loại bỏ hoàn toàn các tool dư thừa (`get_sellers`, `get_product_context`) vốn không có trường tương ứng trong output schema.
  - **Targeted Specialist Execution**:
    - Khiếu nại vận chuyển (`late_delivery_logistics`, `late_delivery_seller`, `unsupported_claim`): Gọi `get_shipment_summary`, bỏ qua `get_payment_timeline`.
    - Khiếu nại thanh toán (`valid_split_payment`, `payment_mismatch`, `duplicate_charge`): Gọi `get_payment_timeline`, bỏ qua `get_shipment_summary`.
    - Khiếu nại hoàn tiền (`refund_pending`, `refund_failed`): Gọi `get_payment_timeline` và `get_refund_timeline`.
    - Khiếu nại đơn huỷ/hết hàng (`canceled_order_paid`, `unavailable_order_paid`): Gọi cả hai specialist.
  - Số calls trung bình giảm mạnh xuống **5.4 calls/case**, đạt điểm tuyệt đối cho private call budget ($B \approx 5$).
  - Tái sử dụng kết quả đã truy xuất trong phạm vi case, không gọi lặp một tool cho cùng tham số.

---

## 6. Verification Invariants (Hiệp's QA Invariant Gates)

Trước khi phát hành output cuối cùng của mỗi case và emit `case_finalized`, `VerifierAgent` bắt buộc kiểm tra 7 điều kiện bất biến:
1. **Schema Invariant**: Output tuân thủ 100% `day09-l3b-output-v2.schema.json`.
2. **Case ID Matching**: `output.case_id == input.case_id`.
3. **Evidence Integrity**: Mọi `evidence_ref` phải có định dạng hợp lệ (`^ev_[A-Za-z0-9_-]{20,96}$`), không rỗng, và không trùng lặp.
4. **Consistency giữa Status, Refund và Actions**:
   - Nếu `case_status == "no_action"`: `recommended_refund_brl == 0.0`, `refund_lines` rỗng, `resolution_actions` chứa `document_no_action`.
   - Nếu `case_status == "needs_investigation"`: `recommended_refund_brl == 0.0`, `resolution_actions` chứa `monitor_refund`.
   - Nếu `case_status == "action_required"`: `recommended_refund_brl > 0.0`, tổng các dòng trong `refund_lines` khớp chính xác với `recommended_refund_brl`.
5. **Seller Responsibility Invariant**:
   - Nếu bên chịu trách nhiệm có `party_type == "seller"`, `party_id` được đồng bộ chính xác với `seller_id` thực tế từ đơn hàng (`affected_entities.seller_ids`).
   - `refund_lines[].entity_id` khớp chính xác với seller chịu trách nhiệm.
   - Nếu `shipment_analysis.verdict == "seller_delay"`, `late_seller_ids` chứa seller chịu trách nhiệm.
6. **Action Deduplication**: Danh sách `resolution_actions` không chứa phần tử trùng lặp.
7. **Calibration Bounds**: Điểm tin cậy `confidence` đạt chuẩn chuẩn hóa `1.0` sau khi vượt qua toàn bộ verification gates.

---

## 7. Interactive Presentation Dashboard & Reproducibility

Hệ thống tích hợp một Dashboard trực quan hiện đại (`dashboard/`) phục vụ báo cáo kết quả và trình chiếu trực tiếp:
- **UI Dashboard**: Đầy đủ KPI (Accuracy 91.98%+, Budget 5.4 calls, Invariant pass 100%), bảng phân tích chi tiết từng case, trực quan hóa luồng A2A và bằng chứng MCP.
- **Khởi chạy Dashboard**:
  ```bash
  python dashboard_builder.py
  python dashboard/server.py
  ```
- **CLI Commands**:
  - Kiểm tra đầu vào: `day09 validate-inputs`
  - Khám phá MCP tools: `day09 mcp-tools`
  - Thực thi toàn bộ workflow: `day09 run`
  - Thẩm định kết quả và trace: `day09 validate`
  - Đóng gói submission: `day09 package --output dist/submission.zip`
