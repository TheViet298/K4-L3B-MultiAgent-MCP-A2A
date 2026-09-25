import json
import glob
import os

os.makedirs('dashboard', exist_ok=True)

inputs_map = {}
for f in sorted(glob.glob('inputs/L3B_CASE_*.json')):
    with open(f, 'r', encoding='utf-8') as fp:
        c = json.load(fp)
    inputs_map[c['case_id']] = c

outputs_map = {}
for f in sorted(glob.glob('outputs/L3B_CASE_*.json')):
    with open(f, 'r', encoding='utf-8') as fp:
        o = json.load(fp)
    outputs_map[o['case_id']] = o

traces_map = {}
with open('traces/trace.jsonl', 'r', encoding='utf-8') as fp:
    for line in fp:
        if line.strip():
            ev = json.loads(line)
            cid = ev['case_id']
            if cid not in traces_map:
                traces_map[cid] = []
            traces_map[cid].append(ev)

case_ids = sorted(inputs_map.keys())

# Metrics & stats
category_counts = {}
total_refund = 0.0
tool_counts = {5: 0, 6: 0}
conflicts_total = 0

for cid in case_ids:
    out = outputs_map.get(cid, {})
    issue = out.get('assessment', {}).get('primary_issue', 'unknown')
    category_counts[issue] = category_counts.get(issue, 0) + 1
    total_refund += out.get('financial_resolution', {}).get('recommended_refund_brl', 0.0)
    conflicts_total += len(out.get('data_conflicts', []))
    
    # tool count
    t_count = sum(1 for e in traces_map.get(cid, []) if e.get('event_type') == 'tool_result_consumed')
    tool_counts[t_count] = tool_counts.get(t_count, 0) + 1

stats = {
    'total_cases': len(case_ids),
    'category_counts': category_counts,
    'total_refund_brl': round(total_refund, 2),
    'tool_call_dist': tool_counts,
    'avg_tool_calls': 5.4,
    'total_conflicts_resolved': conflicts_total,
    'benchmark': {
        'submission_1': {'score': 88.9611, 'efficiency': 55.64, 'consistency': 88.78, 'semantic': 90.84, 'evidence': 85.62, 'calls': 9.0},
        'submission_2': {'score': 90.6607, 'efficiency': 75.40, 'consistency': 88.85, 'semantic': 90.91, 'evidence': 89.89, 'calls': 7.5},
        'submission_3': {'score': 91.9766, 'efficiency': 92.98, 'consistency': 93.92, 'semantic': 90.96, 'evidence': 89.17, 'calls': 5.4}
    }
}

agents_info = {
    'coordinator': {
        'name': 'Coordinator Agent',
        'role': 'Workflow Orchestrator & A2A Session Manager',
        'color': '#6366f1',
        'badge': 'Orchestrator',
        'description': 'Tiếp nhận khiếu nại, phân rã mục tiêu, điều phối phân công nhiệm vụ (task_assigned), quản lý handoff giữa các chuyên gia, kích hoạt xác thực và chốt hồ sơ (case_finalized).'
    },
    'entity_resolver': {
        'name': 'Entity Resolver Agent',
        'role': 'Identity Resolution & Fraud/Spoof Filter',
        'color': '#06b6d4',
        'badge': 'Resolver',
        'description': 'Phân tích ứng viên đơn hàng, đối chiếu lịch sử khách hàng (get_customer_history, get_order), loại bỏ mã giả mạo candidate-xxx, chốt order_id UUID thực tế.'
    },
    'policy_agent': {
        'name': 'Policy Evaluation Agent',
        'role': 'Authoritative Rule Arbiter',
        'color': '#8b5cf6',
        'badge': 'Policy Arbiter',
        'description': 'Truy vấn bộ chính sách chính thức (get_policy: EC_POLICY_V2), phân loại vấn đề cốt lõi (primary issue), quy định điều kiện hoàn tiền và xác định bên chịu trách nhiệm.'
    },
    'order_product_specialist': {
        'name': 'Order & Product Specialist',
        'role': 'Inventory & Seller Specialist',
        'color': '#3b82f6',
        'badge': 'Order Specialist',
        'description': 'Trích xuất chi tiết sản phẩm trong đơn (get_order_items), xác định đúng người bán (seller_id) thực tế của từng món hàng và tính toán giá trị hàng hóa thực tế.'
    },
    'shipment_specialist': {
        'name': 'Shipment Specialist',
        'role': 'Logistics & SLA Inspector',
        'color': '#10b981',
        'badge': 'Logistics Specialist',
        'description': 'Phân tích mốc thời gian vận chuyển (get_shipment_summary), kiểm tra ngày giao hãng vận chuyển, ngày giao khách hàng và hạn bàn giao hàng để xác định trách nhiệm chậm trễ.'
    },
    'payment_refund_specialist': {
        'name': 'Payment & Refund Specialist',
        'role': 'Financial & Reconciliation Auditor',
        'color': '#f59e0b',
        'badge': 'Payment Specialist',
        'description': 'Kiểm tra lịch sử capture thanh toán (get_payment_timeline) và tiến trình hoàn tiền (get_refund_timeline) để phát hiện lệch số tiền, trùng lặp charge hoặc hoàn tiền đang chờ/thất bại.'
    },
    'conflict_resolver': {
        'name': 'Conflict Resolver',
        'role': 'Cross-Source Data Reconciler',
        'color': '#ec4899',
        'badge': 'Data Reconciler',
        'description': 'Phát hiện sự không nhất quán giữa dữ liệu đơn hàng (get_order) và lịch sử khách hàng (get_customer_history), giải quyết theo quy tắc ưu tiên temporal proximity.'
    },
    'verifier': {
        'name': 'Verifier Agent',
        'role': 'Multi-Gate Invariant Auditor',
        'color': '#14b8a6',
        'badge': 'Quality Assurance',
        'description': 'Kiểm định 100% hợp đồng JSON schema, kiểm tra chéo tính nhất quán giữa trạng thái - hành động - hoàn tiền, kiểm tra đồng bộ seller_id và chuẩn hoá calibration.'
    }
}

full_data = {
    'case_ids': case_ids,
    'inputs': inputs_map,
    'outputs': outputs_map,
    'traces': traces_map,
    'stats': stats,
    'agents_info': agents_info
}

output_path = os.path.join('dashboard', 'data.js')
with open(output_path, 'w', encoding='utf-8') as fp:
    fp.write('window.A2A_DATA = ' + json.dumps(full_data, ensure_ascii=False) + ';\n')

print("Successfully generated data.js")
