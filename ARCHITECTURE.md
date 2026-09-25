# L3B Architecture Record

Tài liệu ghi lại toàn bộ quyết định thiết kế hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử (L3B).

## 1. System overview

Luồng điều tra phối hợp giữa các Agent:
```text
Input Case 
   │
   ▼
[Coordinator] ──► [Entity Agent] ──► [Specialists (Shipment & Payment)]
                       │                            │
                       ▼                            ▼
                 (MCP Order/Cust)            (MCP Ship/Pay)
                       │                            │
                       └───────────┬────────────────┘
                                   ▼
                        [Conflict Resolver]
                                   │
                                   ▼
                         [Policy & Settlement]
                                   │
                                   ▼
                          [Verifier Agent] ──► Final Output & Trace
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | `candidate_order_ids`, `claimed_order_id`, `customer_hint` | Resolve candidate ID, trích xuất customer context & affected entities | `get_order`, `get_customer_history` | `entity_resolution`, `customer_context`, `affected_entities` |
| Coordinator | Raw input case JSON | Khởi tạo phiên, quản lý lifecycle, phân phối task & handoff | None (chỉ quản lý Trace) | Handoff qua từng stage |
| Shipment | `resolved_order_ids`, order shipment data | Phân tích mốc thời gian giao nhận, seller delay vs carrier delay | `get_shipment`, `get_tracking` | `shipment_analysis` (verdict, late_seller_ids, timeline_complete) |
| Payment/refund | `resolved_order_ids`, payment ledger | Đối soát captured total, refunded total, refundable total | `get_payment_details`, `get_refund_status` | `payment_analysis` (verdict, financial totals) |
| Conflict resolver | Phản ánh khách vs Bưu cục/Cổng thanh toán | Phát hiện mâu thuẫn dữ liệu, giải quyết theo source precedence | None | `data_conflicts` |
| Policy | Kết quả từ các Specialist và Conflict resolver | Suy ra primary_issue, root_cause_analysis, financial_resolution, actions | `get_policy` (nếu có) | Đánh giá tổng quan vụ việc |
| Verifier | Draft JSON output từ Policy | Kiểm tra 7 Invariant gates, schema validation, calibration, auto-repair | None | Final verified output, emit `verification_completed` |

## 3. Entity resolution và A2A protocol

- **Định danh candidate**: Đối chiếu `candidate_order_ids` với `claimed_order_id` và kết quả truy vấn tool order.
- **Phân loại trạng thái**:
  - `resolved`: Tìm thấy đúng 1 order hợp lệ (confidence 0.95).
  - `ambiguous`: Tồn tại nhiều candidate hợp lệ không phân biệt được (confidence 0.60).
  - `not_found`: Không candidate nào hợp lệ (confidence 0.85).
- **Handoff & Correlation**: Sử dụng `case_id` làm correlation key xuyên suốt phiên. Tránh loop bằng pipeline một chiều (DAG). Không đưa chain-of-thought vào trace.

## 4. Evidence và conflict lifecycle

- **Thu thập & Cache**: `CaseCache` bọc gateway call, lưu evidence theo `(tool, kwargs)`. Mỗi evidence có `evidence_ref` hợp lệ dạng `ev_[A-Za-z0-9_-]{20,96}`.
- **Trace linkage**: Mọi evidence khi sử dụng để ra quyết định đều emit event `tool_result_consumed` với `evidence_refs` tương ứng.
- **Source precedence policy**:
  - `Official carrier logs` (Dấu mốc bưu cục) được ưu tiên hơn `customer statement` khi có tranh chấp giao nhận.
  - `Payment gateway ledger` được ưu tiên hơn `order metadata` khi có tranh chấp số tiền.
- **Zero-leaking invariant**: Tuyệt đối không sử dụng `evidence_ref` giữa các case khác nhau.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | 2 retries | Sử dụng dữ liệu có sẵn trong case snapshot | `tool_fallback_used` |
| Entity not found/ambiguous | 0 retries | Đánh dấu status `not_found`/`ambiguous`, dùng hint | `entity_status_decided` |
| Source conflict | 0 retries | Áp dụng Source Precedence Rule | `conflict_resolved` |
| Invalid specialist result | 1 retry | Fallback sang `insufficient_evidence` | `specialist_fallback` |

- **Call budget & Cache**: Tối đa 6 calls/case để bảo toàn điểm Efficiency (5%). Bỏ qua các call thừa khi đã đủ bằng chứng kết luận.

## 6. Verification invariants (Hiệp's QA Invariant Gates)

Trước khi emit `case_finalized`, `VerifierAgent` bắt buộc kiểm tra 7 điều kiện bất biến:
1. **Case ID Matching**: `output.case_id == input.case_id`.
2. **Status vs Refund Consistency**: Nếu `case_status == "no_action"`, `recommended_refund_brl` bắt buộc bằng 0 và không có refund actions.
3. **Financial Conservation**: `recommended_refund_brl == sum(refund_lines.amount_brl) <= refundable_total_brl`.
4. **Seller Delay Consistency**: Nếu `shipment_analysis.verdict == "seller_delay"`, `late_seller_ids` không được rỗng và `responsible_parties` phải có ít nhất 1 seller.
5. **Carrier Delay Consistency**: Nếu `shipment_analysis.verdict == "logistics_delay"`, `responsible_parties` phải có `logistics_provider`.
6. **Confidence Calibration**: Giới hạn trong khoảng `[0.05, 0.95]`; nếu `insufficient_evidence` thì confidence $\le 0.40$.
7. **Strict JSON Schema Compliance**: Vượt qua `Draft202012Validator` với `l3b-output-v2.schema.json`.

## 7. Reproducibility

- **Runtime**: Python >= 3.11.
- **Dependencies**: `httpx2`, `jsonschema`, `referencing`, `pytest`, `pytest-asyncio`.
- **Concurrency limit**: Sequential hoặc Semaphore(5) khi chạy toàn bộ 100 cases.
- **Run command**: `day09 run` và kiểm tra với `day09 validate`.
