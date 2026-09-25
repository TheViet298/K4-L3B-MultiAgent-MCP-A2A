# L3B Architecture Record — Multi-Agent MCP + A2A

Tài liệu thiết kế hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử Olist (L3B).

---

## 1. System Overview

Hệ thống hoạt động theo mô hình **Supervisor / Router Điều Phối & Các Worker Chuyên Sâu** được điều phối bằng **LangGraph (`StateGraph`)**:

```text
Input Case (AgentState)
   │
   ▼
[router] ──► [entity_agent] ──┬──► [shipment_specialist] (Logistics) ──┐
                              │                                        │ (Parallel Fan-in)
                              └──► [payment_specialist] (Financial)  ──┴──► [claim_assessor]
                                                                                   │
                                                                                   ▼
                                                                        [conflict_resolver]
                                                                                   │
                                                                                   ▼
                                                                           [policy_agent]
                                                                                   │
                                                                                   ▼
                                                                              [verifier] ──► Final Output & Trace
```

Toàn bộ các Worker giao tiếp với Olist Database thông qua **MCP Gateway** (có caching & audit) và phát các sự kiện vòng đời chuẩn chỉ qua **TraceWriter**. LangGraph quản lý trạng thái (`AgentState`), fan-out/fan-in song song giữa các Specialist và đảm bảo tính bất biến của dữ liệu.

---

## 2. Agent Ownership

| Actor | Thành viên | Input | Trách nhiệm | Tool permission | Output / Handoff |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Supervisor / Router** | **Ngô Thế Việt** | Raw case JSON | Khởi tạo phiên, trích xuất candidate, điều phối luồng A2A, tổng hợp phán quyết | Không gọi DB trực tiếp | `case_received`, task assignments, `case_finalized` |
| **Policy Worker** | **Nguyễn Quang Đạo** | `product_category`, `order_date`, `claim_date` | Tra cứu quy chế Olist (7 ngày đổi trả, 30 ngày bảo hành kỹ thuật), trích dẫn điều khoản | `get_policy_clause`, `get_category_rules` | `policy_verdict`, `applicable_clauses`, `evidence_refs` |
| **Logistics Worker** | **Nguyễn Văn Giáp** | `order_id`, candidate orders | Đối soát timeline giao nhận (`orders`, `order_items`, `shipments`), phân định lỗi Shipper vs Seller | `get_order`, `get_order_items`, `get_shipment_summary` | `shipment_analysis` (verdict, late_seller_ids, timeline_complete) |
| **Financial Worker** | **Cao Đức Hiệp** | `order_id`, payment ledger | Truy vấn `order_payments`, đối soát captured total, refunded total, refundable total | `get_payment_details`, `get_refund_status` | `payment_analysis`, `financial_resolution` |
| **Conflict Resolver** | **Cả nhóm** | Lời khai khách vs Log hệ thống | Phát hiện mâu thuẫn dữ liệu, giải quyết theo Source Precedence | Không dùng tool | `data_conflicts` |
| **Verifier Agent** | **Cao Đức Hiệp** | Draft output JSON | Kiểm tra 7 Invariant gates, schema validation, calibration, auto-repair | Không dùng tool | Final verified output, emit `verification_completed` |

---

## 3. Entity Resolution & A2A Protocol

- **Định danh candidate**: Đối chiếu `candidate_order_ids` với `claimed_order_id` và kết quả truy vấn tool order.
- **Phân loại trạng thái**:
  - `resolved`: Tìm thấy đúng 1 order hợp lệ (confidence $\ge 0.85$).
  - `ambiguous`: Tồn tại nhiều candidate hợp lệ không phân biệt được (confidence 0.60).
  - `not_found`: Không candidate nào hợp lệ (confidence 0.85).
- **Handoff & Correlation**: Sử dụng `case_id` làm correlation key xuyên suốt phiên. Tránh loop bằng pipeline một chiều (DAG). Không đưa chain-of-thought vào trace.

---

## 4. Evidence & Conflict Lifecycle

- **Thu thập & Cache**: `CaseCache` bọc gateway call, lưu evidence theo `(tool, kwargs)`. Mỗi evidence có `evidence_ref` hợp lệ dạng `ev_[A-Za-z0-9_-]{20,96}`.
- **Trace linkage**: Mọi evidence khi sử dụng để ra quyết định đều emit event `tool_result_consumed` với `evidence_refs` tương ứng.
- **Source Precedence Policy**:
  - `Official carrier logs` (Dấu mốc bưu cục) được ưu tiên hơn `customer statement` khi có tranh chấp giao nhận.
  - `Payment gateway ledger` được ưu tiên hơn `order metadata` khi có tranh chấp số tiền.
- **Zero-leaking invariant**: Tuyệt đối không tái sử dụng `evidence_ref` giữa các case khác nhau.

---

## 5. Failure and Efficiency Policy

| Failure Scenario | Retry Budget | Fallback Strategy | Trace Event / Code |
| :--- | :---: | :--- | :--- |
| **MCP Timeout / Error** | 2 retries | Sử dụng dữ liệu có sẵn trong case snapshot | `tool_fallback_used` |
| **Entity Not Found / Ambiguous** | 0 retries | Đánh dấu status `not_found`/`ambiguous`, dùng hint | `entity_status_decided` |
| **Source Conflict** | 0 retries | Áp dụng Source Precedence Rule | `conflict_resolved` |
| **Invalid Specialist Result** | 1 retry | Fallback sang `insufficient_evidence` | `specialist_fallback` |

* **Call budget & Cache**: Tối đa 6 calls/case để bảo toàn điểm Efficiency (5%). Bỏ qua các call thừa khi đã đủ bằng chứng kết luận.

---

## 6. Verification Invariants (Hiệp's QA Invariant Gates)

Trước khi emit `case_finalized`, `VerifierAgent` bắt buộc kiểm tra 7 điều kiện bất biến:
1. **Case ID Matching**: `output.case_id == input.case_id`.
2. **Status vs Refund Consistency**: Nếu `case_status == "no_action"`, `recommended_refund_brl` bắt buộc bằng 0 và không có refund actions.
3. **Financial Conservation**: $\text{recommended\_refund\_brl} = \sum \text{refund\_lines.amount\_brl} \le \text{refundable\_total\_brl}$.
4. **Seller Delay Consistency**: Nếu `shipment_analysis.verdict == "seller_delay"`, `late_seller_ids` không được rỗng và `responsible_parties` phải có ít nhất 1 seller.
5. **Carrier Delay Consistency**: Nếu `shipment_analysis.verdict == "logistics_delay"`, `responsible_parties` phải có `logistics_provider`.
6. **Confidence Calibration**: Giới hạn trong khoảng $[0.05, 0.95]$; nếu `insufficient_evidence` thì confidence $\le 0.40$.
7. **Strict JSON Schema Compliance**: Vượt qua `Draft202012Validator` với `l3b-output-v2.schema.json`.

---

## 7. Reproducibility & Environment

* **Runtime:** Python $\ge 3.11$.
* **Dependencies:** `langgraph`, `httpx2`, `jsonschema`, `referencing`, `pytest`, `pytest-asyncio`.
* **Concurrency limit:** Sequential hoặc Semaphore(5) khi chạy toàn bộ 100 cases.
* **Run command:** `day09 run` và kiểm tra với `day09 validate`.
