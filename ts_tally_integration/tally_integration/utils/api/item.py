import frappe
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_master_sync_limit
import json
from datetime import datetime
from werkzeug.wrappers import Response
from frappe.utils import get_datetime

@frappe.whitelist()
@company_sync_required("STOCKITEMS")
def get_item(company_id = None):
    if company_id == None:
        return Response(json.dumps("Company number not found!", default=str), content_type='application/json')

    enable_sync = frappe.get_value('Voucher Sync Control', {'voucher_name': 'Item'}, ['enable_sync'])
    if not enable_sync:
        final_voucher = {
            "status": True,
            "VOUCHERDETAILS": {
                "STOCKITEMS": []
                }
            }

        return Response(json.dumps(final_voucher, default=str), content_type='application/json')

    company_name = frappe.get_value('TS Tally Company', company_filters(company_id), ['company_name'])

    all_doc = []

    sync_master_from = frappe.get_value('TS Tally Company',company_filters(company_id),'sync_master_from')

    start_date = get_datetime(sync_master_from)

    synced_items = frappe.get_all('Tally Master Sync Log',
        filters={'parenttype': 'Item', 'company_number': company_id, 'status': 'SUCCESS'},
        pluck='parent')

    items = frappe.get_all('Item', filters={'disabled': 0, 'has_variants': 0, 'name': ['not in', synced_items], 'creation': [">", start_date]}, fields=['*'], limit=get_master_sync_limit())

    for item in items:

        tax_template = frappe.get_all('Item Tax', filters={'parent': item.name}, fields=['*'])

        # Default taxability
        taxability = ""
        item_tax_template = []

        for i in tax_template:
            item_tax_template = frappe.get_all('Item Tax Template', filters={'company': company_name, 'name': i.item_tax_template}, fields=['*'])
            if item_tax_template:
                taxability = 'Taxable' if item_tax_template[0]['gst_treatment'] == 'Taxable' else ""
            else:
                taxability = ""

        hsn_desc = frappe.get_value('GST HSN Code', {'name': item.gst_hsn_code}, 'description') or ''

        is_gst_applicable = "Applicable" if taxability == 'Taxable' else 'Not Applicable'
        hsn_code = item.gst_hsn_code if is_gst_applicable == 'Applicable' else ''
        hsn_desc_clean = hsn_desc.replace('\n', ' ') if is_gst_applicable == 'Applicable' else ''
        gst_type_of_supply = "Goods" if is_gst_applicable == 'Applicable' else ''
        cgst = "{:.1f}".format(item_tax_template[0]['gst_rate'] / 2) if item_tax_template and is_gst_applicable == 'Applicable' else ""
        sgst = "{:.1f}".format(item_tax_template[0]['gst_rate'] / 2) if item_tax_template and is_gst_applicable == 'Applicable' else ""
        igst = "{:.1f}".format(item_tax_template[0]['gst_rate']) if item_tax_template and is_gst_applicable == 'Applicable' else ""
        clean_item_name = item.item_code.strip() if item.item_code else item.name.strip()
        item_dict = {
            "Autoid": clean_item_name,
            "CompanyNumber": str(company_id),
            "Name": clean_item_name,
            "Parent": item.item_group,
            "Category": "",
            "BaseUnits": item.stock_uom,
            "IsBatchWiseOn": 'Yes' if item.has_batch_no else "No",
            "IsGSTApplicable": is_gst_applicable,
            "HsnCode": hsn_code,
            "Hsn": hsn_desc_clean,
            "Taxability": taxability if is_gst_applicable == 'Applicable' else '',
            "CgstRate": cgst,
            "SgstRate": sgst,
            "IgstRate": igst,
            "GSTTypeofSupply": gst_type_of_supply,
            "GodownName": "",
            "BatchName": "",
            "OpeningBalance": "",
            "OpeningRate": "",
            "OpeningValue": ""
        }


        all_doc.append(item_dict)
    final_voucher = ({
        "status": True,
        "VOUCHERDETAILS": {
            "STOCKITEMS": all_doc
        }
    })


    final_voucher = Response(json.dumps(final_voucher, default=str), content_type='application/json')
    final_voucher.status_code = 200


    return final_voucher



@frappe.whitelist()
def fetch_response(response=None, company_id=None):
    data = json.loads(response) if isinstance(response, str) else response
    items = data.get("STOCKITEM RESPONSE", [])

    company_name = frappe.get_value('TS Tally Company', {'company_number': company_id}, 'company_name') if company_id else None

    for item in items:
        item_name = item.get("AUTOID")
        status = item.get("STATUS")
        import_date = item.get("IMPORTDATE")
        import_time = item.get("IMPORTTIME")

        if not item_name:
            continue

        item_docname = frappe.db.get_value("Item", {"item_code": item_name}, "name")

        if item_docname:
            sync_time = datetime.combine(
                datetime.strptime(import_date, "%Y%m%d").date(),
                datetime.strptime(import_time, "%H:%M:%S").time()
            )

            existing = frappe.db.get_value('Tally Master Sync Log', {
                'parent': item_docname,
                'parenttype': 'Item',
                'company_number': company_id
            }, 'name')

            if existing:
                frappe.db.set_value('Tally Master Sync Log', existing, {
                    'tally_auto_id': item_name,
                    'status': status,
                    'sync_time': sync_time
                })
            else:
                max_idx = frappe.db.count('Tally Master Sync Log', {
                    'parent': item_docname,
                    'parenttype': 'Item'
                })

                sync_log = frappe.new_doc('Tally Master Sync Log')
                sync_log.parent = item_docname
                sync_log.parenttype = 'Item'
                sync_log.parentfield = 'custom_tally_sync_log'
                sync_log.idx = max_idx + 1
                sync_log.company_number = company_id
                sync_log.company_name = company_name
                sync_log.tally_auto_id = item_name
                sync_log.status = status
                sync_log.sync_time = sync_time
                sync_log.db_insert()

            frappe.db.set_value('Item', item_docname, 'modified', frappe.utils.now())

    frappe.db.commit()

    response = {
        "status":True,
        "message":"Updated successfully"
    }
    return Response(json.dumps(response, default=str), content_type='application/json')

