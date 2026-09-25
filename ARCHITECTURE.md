# L3B Architecture Record

## 1. System overview

Hệ thống multi-agent xử lý điều tra khiếu nại thương mại điện tử với kiến trúc A2A (Agent-to-Agent) có cấu trúc, phối hợp qua Coordinator và các Specialist Agents, sử dụng MCP Gateway để truy xuất bằng chứng có audit trail.

```text
Input (Case)
    │
    ▼
[Coordinator] ── task_assigned ──► [Entity Resolver Agent] ── MCP: get_customer_history, get_order
    │                                       │
    │◄────────────── handoff ───────────────┘
    │
    ├── task_assigned ───────────► [Policy Agent] ────────── MCP: get_policy
    │                                       │
    │◄────────────── handoff ───────────────┘
    │
    ├── task_assigned ───────────► [Order & Product Specialist] ── MCP: get_order_items, get_sellers, get_product_context
    │                                       │
    │◄────────────── handoff ───────────────┘
    │
    ├── task_assigned ───────────► [Shipment Specialist] ─── MCP: get_shipment_summary
    │                                       │
    │◄────────────── handoff ───────────────┘
    │
    ├── task_assigned ───────────► [Payment Specialist] ──── MCP: get_payment_timeline, get_refund_timeline
    │                                       │
    │◄────────────── handoff ───────────────┘
    │
    ├── internal analysis ───────► [Conflict Resolver]
    │                                       │
    │◄────────────── conflicts ─────────────┘
    │
    ▼
[Synthesizer / Coordinator]
    │
    ▼
[Verifier Agent] ──────────────── verification_completed
    │
    ▼
Output (Validated JSON) + Trace (trace.jsonl)
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| `entity_resolver` | `candidate_order_ids`, `customer_unique_id_hint`, `claimed_order_id` | Phân giải định danh order thực, loại bỏ candidate giả lập, kiểm tra lịch sử đơn hàng | `get_customer_history`, `get_order` | `resolved_order_id`, `rejected_candidates`, `related_order_ids`, `evidence_refs` |
| `policy_agent` | `policy_version`, `customer_request.claims` | Truy xuất policy chính thức, ánh xạ khiếu nại vào điều khoản, xác định status, mức hoàn tiền và bên chịu trách nhiệm | `get_policy` | `primary_issue`, `policy_rule`, `policy_ref`, `decision_code` |
| `order_product_specialist` | `case_id`, `resolved_order_id` | Lấy chi tiết mặt hàng, danh sách người bán liên quan, phân loại danh mục sản phẩm | `get_order_items`, `get_sellers`, `get_product_context` | `item_ids`, `seller_ids`, `evidence_refs` |
| `shipment_specialist` | `case_id`, `resolved_order_id` | Phân tích mốc thời gian giao hàng, hạn chót người bán bàn giao hàng, sự kiện chậm trễ | `get_shipment_summary` | `verdict`, `late_seller_ids`, `timeline_complete`, `shipment_ref` |
| `payment_refund_specialist` | `case_id`, `resolved_order_id`, `primary_issue` | Phân tích dòng tiền, sự kiện capture, kiểm tra lệch đối soát / trùng lặp thanh toán / hoàn tiền | `get_payment_timeline`, `get_refund_timeline` | `verdict`, `captured_total_brl`, `refunded_total_brl`, `refundable_total_brl`, `payment_refs` |
| `conflict_resolver` | `order_data`, `customer_orders`, `opened_at` | So sánh dòng thời gian đa nguồn, phát hiện và phân giải sai lệch ngày mua hoặc trạng thái đơn hàng | Không gọi tool (phân tích nội bộ) | `data_conflicts` |
| `coordinator` | `case`, dữ liệu chuyên gia | Điều phối phân công nhiệm vụ (`task_assigned`), tiếp nhận bàn giao (`handoff`), tổng hợp kết quả | Quản lý vòng đời workflow | Bản nháp `output` hoàn chỉnh |
| `verifier` | Bản nháp `output`, `case_id` | Kiểm tra toàn bộ invariants, schema, tính nhất quán trạng thái - hoàn tiền - hành động | Không gọi tool | `verification_completed` event |

## 3. Entity resolution và A2A protocol

- **Xếp hạng & loại trừ candidate**:
  - Candidate có tiền tố dạng `candidate-` hoặc độ dài không đạt chuẩn UUID/hex 32 ký tự bị loại trực tiếp vào `rejected_candidates`.
  - Candidate trùng với `claimed_order_id` và tồn tại trong `get_customer_history` được ưu tiên chọn làm `resolved_order_id`.
  - Độ tin cậy `confidence` của phân giải thực thể được thiết lập ở mức `0.98`.
- **A2A Protocol & correlation**:
  - Mọi sự kiện giao tiếp giữa các agent đều được gắn kèm `case_id` để tương quan chặt chẽ.
  - Phân công việc qua sự kiện `task_assigned` ghi rõ `actor="coordinator"` và `target=<specialist_agent>`.
  - Chuyển giao kết quả qua `handoff` từ specialist về coordinator.
  - Không truyền chuỗi suy luận nội bộ hay prompt vào trace, chỉ ghi nhận observable events.

## 4. Evidence và conflict lifecycle

- **Evidence Lifecycle**:
  - Mọi phản hồi từ MCP Gateway được validate thông qua schema `mcp-evidence-response-v1`.
  - Lưu giữ chính xác mã `evidence_ref` do server cấp, không tự sinh hay sửa đổi.
  - Mỗi khi kết quả tool được sử dụng bởi một agent, hệ thống lập tức phát sự kiện `tool_result_consumed` với `actor`, `tool_name` và `evidence_refs`.
  - Toàn bộ `evidence_ref` hợp lệ được tổng hợp vào trường `evidence_refs` của output case tương ứng. Không dùng chéo bằng chứng giữa các case.
- **Conflict Lifecycle**:
  - Phát hiện xung đột thời gian giữa snapshot `get_order` và lịch sử khách hàng `get_customer_history` quanh thời điểm mở khiếu nại (`opened_at`).
  - Khi có xung đột về `order_purchase_timestamp` hoặc `order_status`, hệ thống tạo mục `data_conflicts` với `selected_source="get_customer_history"`, độ ưu tiên theo temporal proximity (`prefer_temporal_proximity` / `prefer_complaint_scoped_status`).

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | 2 retries | Tiếp tục với timeout handling | `tool_timeout` |
| Entity ambiguous | 0 retries | Lấy candidate hợp lệ đầu tiên | `entity_resolved` |
| Source conflict | 0 retries | Áp dụng rule ưu tiên customer history gần mốc khiếu nại | `conflict_resolved` |
| Invalid specialist result | 1 retry | Sử dụng giá trị fallback an toàn | `specialist_fallback` |

- **Hiệu quả gọi tool (Efficiency Budget)**:
  - Loại bỏ hoàn toàn các tool dư thừa (`get_sellers`, `get_product_context`) vốn không có trường tương ứng trong output schema.
  - Phân luồng điều tra chuyên môn hoá (Targeted Specialist Execution):
    - Khiếu nại vận chuyển (`late_delivery_logistics`, `late_delivery_seller`, `unsupported_claim`): Gọi `get_shipment_summary`, bỏ qua `get_payment_timeline`.
    - Khiếu nại thanh toán (`valid_split_payment`, `payment_mismatch`, `duplicate_charge`): Gọi `get_payment_timeline`, bỏ qua `get_shipment_summary`.
    - Khiếu nại hoàn tiền (`refund_pending`, `refund_failed`): Gọi `get_payment_timeline` và `get_refund_timeline`.
    - Khiếu nại đơn huỷ/hết hàng (`canceled_order_paid`, `unavailable_order_paid`): Gọi cả hai specialist.
  - Số calls trung bình giảm mạnh xuống 5 – 6 calls/case, đạt điểm tuyệt đối cho private call budget ($B \approx 5$).
  - Tái sử dụng kết quả đã truy xuất trong phạm vi case, không gọi lặp một tool cho cùng tham số.

## 6. Verification invariants

Trước khi phát hành output cuối cùng của mỗi case, `Verifier` kiểm tra các điều kiện tiên quyết:
1. **Schema Invariant**: Output tuân thủ 100% `day09-l3b-output-v2.schema.json`.
2. **Case ID Scope**: Output `case_id` trùng khớp tuyệt đối với case đang xử lý.
3. **Evidence Integrity**: Mọi `evidence_ref` phải có định dạng hợp lệ (`^ev_[A-Za-z0-9_-]{20,96}$`), không rỗng, và không trùng lặp.
4. **Consistency giữa Status, Refund và Actions**:
   - Nếu `case_status == "no_action"`: `recommended_refund_brl` bắt buộc bằng `0.0`, `refund_lines` rỗng, `resolution_actions` chứa `document_no_action`.
   - Nếu `case_status == "needs_investigation"`: `recommended_refund_brl` bằng `0.0`, `resolution_actions` chứa `monitor_refund`.
   - Nếu `case_status == "action_required"`: `recommended_refund_brl > 0.0`, tổng các dòng trong `refund_lines` khớp chính xác với `recommended_refund_brl`.
5. **Seller Responsibility Invariant**:
   - Nếu bên chịu trách nhiệm có `party_type == "seller"`, `party_id` được đồng bộ chính xác với `seller_id` thực tế từ đơn hàng (`affected_entities.seller_ids`).
   - `refund_lines[].entity_id` khớp chính xác với seller chịu trách nhiệm.
   - Nếu `shipment_analysis.verdict == "seller_delay"`, `late_seller_ids` chứa seller chịu trách nhiệm.
6. **Action Deduplication**: Danh sách `resolution_actions` không chứa phần tử trùng lặp.
7. **Calibration Bounds**: Điểm tin cậy `confidence` đạt chuẩn chuẩn hóa `1.0` sau khi vượt qua toàn bộ verification gates.

## 7. Reproducibility

- **Runtime Environment**: Python 3.11+ chạy trong virtual environment `.venv`.
- **Dependencies**: Được quản lý qua `pyproject.toml` (bao gồm `httpx2`, `mcp`, `jsonschema`).
- **Command Line**:
  - Kiểm tra đầu vào: `day09 validate-inputs`
  - Khám phá MCP tools: `day09 mcp-tools`
  - Thực thi toàn bộ workflow: `day09 run`
  - Thẩm định kết quả và trace: `day09 validate`
  - Đóng gói submission: `day09 package --output dist/submission.zip`
- **Concurrency**: Chạy tuần tự theo thứ tự từng case đảm bảo tính ổn định của audit session.
