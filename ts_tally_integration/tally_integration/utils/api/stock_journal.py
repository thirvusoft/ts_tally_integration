import frappe
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_voucher_sync_limit
from datetime import datetime
import json
from werkzeug.wrappers import Response
from frappe.utils import getdate, today


@frappe.whitelist()
@company_sync_required("VOUCHER")
def get_stock_entry(company_id=None):
    import json
    from frappe.utils.response import Response
    from datetime import datetime

    if not company_id:
        return Response(json.dumps('Company Number not found!', default=str), content_type='application/json')

    stock = frappe.get_value('TS Tally Company', company_filters(company_id), 'stock')
    if stock == 'Non-Inventory':
        return Response(json.dumps({
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": []
            }
        }, default=str), content_type='application/json')

    enable_sync = frappe.get_value('Voucher Sync Control', {'voucher_name': 'Stock Journal'}, ['enable_sync'])
    if not enable_sync:
        final_voucher = {
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": []
                }
            }
        return Response(json.dumps(final_voucher, default=str), content_type='application/json')

    company_name = frappe.get_value('TS Tally Company', company_filters(company_id), 'company_name')


    all_vouchers = []

    sync_from = frappe.get_value('TS Tally Company',company_filters(company_id),'sync_from')

    start_date = getdate(sync_from)
    sync_to = frappe.get_value('TS Tally Company', company_filters(company_id), 'sync_to')
    end_date = getdate(sync_to) if sync_to else getdate(today())

    stock_entries = frappe.get_all('Stock Entry',
                                   filters={'company': company_name, 'docstatus': 1,
                                            'custom_tally_guid': ['is', 'not set'], 'posting_date': ['between', [start_date, end_date]]},
                                   fields=['name', 'posting_date'], order_by='posting_date asc', limit=get_voucher_sync_limit()
                                   )

    # -------------------------------
    # FIX: Predefine response_data
    # -------------------------------
    response_data = {
        "status": True,
        "VOUCHERDETAILS": {
            "VOUCHER": all_vouchers
        }
    }

    for entry in stock_entries:
        stock_entry = frappe.get_doc('Stock Entry', entry.name)
        for item in stock_entry.items:
            base_data = {
                "Autoid": str(item.item_code),
                "CompanyNumber": str(company_id),
                "TallyMasterid": 1,
                "Voucherid": stock_entry.name,
                "VoucherNumber": stock_entry.name,
                "VoucherDate": datetime.strptime(str(stock_entry.posting_date), '%Y-%m-%d').strftime('%Y%m%d'),
                "VoucherType": "ERP StockJournal",
                "VoucherTypeParent": "StockJournal",
                "LedgerName": "",
                "LedgerParent": "",
                "LedgerAddress": "",
                "LedgerState": "",
                "LedgerCountry": "",
                "LedgerPincode": "",
                "LedgerMobile": "",
                "LedgerGstReg": "",
                "LedgerGstin": "",
                "BillName": "",
                "BillDate": "",
                "CostCategory": "",
                "CostCentre": "",
                "Stockitem": item.item_code,
                "BatchNo": "",
                "Quantity": float(item.qty),
                "Rate": float(item.basic_rate),
                "Discount": "",
                "Amount": float(item.amount),
                "BuyerName": "",
                "BuyerMailingName": "",
                "BuyerAddress1": "",
                "BuyerAddress2": "",
                "BuyerState": "",
                "BuyerCountry": "",
                "ConsigneeName": "",
                "ConsigneeMailingName": "",
                "ConsigneeAddress1": "",
                "ConsigneeAddress2": "",
                "ConsigneeState": "",
                "ConsigneeCountry": "",
                "Narration": stock_entry.remarks or ""
            }

            if item.s_warehouse and item.t_warehouse:
                all_vouchers.append({
                    **base_data,
                    "CrDr": "Cr",
                    "Godown": item.s_warehouse.split('-')[0].strip(),
                    "Void": "out"
                })
                all_vouchers.append({
                    **base_data,
                    "CrDr": "Dr",
                    "Godown": item.t_warehouse.split('-')[0].strip(),
                    "Void": "in"
                })

            elif item.s_warehouse:
                all_vouchers.append({
                    **base_data,
                    "CrDr": "Cr",
                    "Godown": item.s_warehouse.split('-')[0].strip(),
                    "Void": "out"
                })

            elif item.t_warehouse:
                all_vouchers.append({
                    **base_data,
                    "CrDr": "Dr",
                    "Godown": item.t_warehouse.split('-')[0].strip(),
                    "Void": "in"
                })

    # Always safe now
    return Response(json.dumps(response_data, default=str), content_type='application/json')





@frappe.whitelist()
def fetch_response(response):
    data = json.loads(response) if isinstance(response, str) else response
    stockjournal_response = data.get("STOCKJOURNAL RESPONSE", [])

    for response in stockjournal_response:
        stock_entry_id = response.get("AUTOID")
        guid = response.get("GUID")
        ref_no = response.get("REFNO")
        import_date = response.get("IMPORTDATE")
        import_time = response.get("IMPORTTIME")

        existing_stock_entry = frappe.db.get_value("Stock Entry", {"name": stock_entry_id}, "name")

        import_date = datetime.strptime(import_date, "%Y%m%d").date()
        import_time = datetime.strptime(import_time, "%H:%M:%S").time()

        frappe.db.set_value("Stock Entry", existing_stock_entry, {
            "custom_tally_auto_id": stock_entry_id,
            "custom_tally_guid": guid,
            "custom_tally_refno": ref_no,
            "custom_sync_time": datetime.combine(import_date, import_time)
        })

    frappe.db.commit()

    response = {
        "status":True,
        "message":"Updated successfully"
    }

    return Response(json.dumps(response, default=str), content_type='application/json')
