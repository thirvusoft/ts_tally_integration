import frappe, importlib
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_voucher_sync_limit
from datetime import datetime
import json
from werkzeug.wrappers import Response
from frappe.utils import getdate, today


DEFAULT_COST_CATEGORY = "Primary Cost Category"


def get_tally_cost_center(doc):
    return doc.cost_center.split("-", 1)[0].strip() if doc.cost_center else ""

def get_tally_cost_category(doc):
    return DEFAULT_COST_CATEGORY if get_tally_cost_center(doc) else ""

EMPTY_ADDRESS = {
    "city": "",
    "state": "",
    "country": "",
    "pincode": "",
    "phone": "",
    "address_line1": "",
    "address_line2": "",
    "address_title": "",
}


@frappe.whitelist()
def get_sales_inv(company_id=None):
    redirect_record = frappe.get_value(
        'Tally Different Function',
        {
            'parent': 'TS Tally Settings',
            'tally_function_name': 'get_sales_inv'
        },
        'redirection_path'
    )

    if redirect_record:

        module_path, func_name = redirect_record.rsplit('.', 1)

        custom_module = importlib.import_module(module_path)

        func = getattr(custom_module, func_name)

        return func(company_id)

    else:

        if company_id == None:
            return Response(json.dumps('Company Number not found!', default=str), content_type='application/json')

        stock = frappe.get_value('TS Tally Company', {'company_number': company_id}, ['stock'])
        if stock == 'Non-Inventory':
            empty = ({
                "status": True,
                "VOUCHERDETAILS": {
                    "VOUCHER": []
                    }
                })
            return Response(json.dumps(empty, default=str), content_type='application/json')

        enable_sync = frappe.get_value('Voucher Sync Control', {'voucher_name': 'Sales Invoice (Inventory)'}, ['enable_sync'])
        if not enable_sync:
            final_voucher = {
                "status": True,
                "VOUCHERDETAILS": {
                    "VOUCHER": []
                    }
                }
            return Response(json.dumps(final_voucher, default=str), content_type='application/json')

        company_name = frappe.get_value('TS Tally Company', {'company_number': company_id}, ['company_name'])
        cost_center = frappe.get_value('TS Tally Company', {'company_number': company_id}, ['cost_center'])
        default_warehouse = frappe.db.get_single_value('TS Tally Settings', 'default_warehouse')

        company_address_link = frappe.get_all('Dynamic Link', filters={'link_doctype': 'Company', 'link_name': company_name}, fields=['parent'])
        company_address = frappe.get_all(
            'Address',
            filters={'name': company_address_link[0]['parent']} if company_address_link else {'name': '__missing__'},
            fields=['*']
        )
        company_address = company_address or [EMPTY_ADDRESS.copy()]
        company_gst = frappe.get_value('Company', {'name': company_name}, ['gstin'])

        all_vouchers = []

        sync_from = frappe.get_value('TS Tally Company',{'company_number': company_id},'sync_from')

        start_date = getdate(sync_from)
        sync_to = frappe.get_value('TS Tally Company', {'company_number': company_id}, 'sync_to')
        end_date = getdate(sync_to) if sync_to else getdate(today())
        sales_filters = {'company':company_name, 'is_return':0, 'docstatus':1, 'is_opening': 'No',
                                             'custom_tally_guid': ['is', 'not set'], 'posting_date': ['between', [start_date, end_date]]}
        if cost_center:
            sales_filters['cost_center'] = cost_center
        sales_list = frappe.get_all('Sales Invoice',
                                    filters=sales_filters,
                                    fields=['*'], order_by='posting_date asc', limit=get_voucher_sync_limit())

        for doc in sales_list:
            tax_processed = False
            if doc.customer_address:
                cus_address = frappe.get_all('Address', filters={'name': doc.customer_address}, fields=['*'])
            else:
                cus_address = []
            cus_address = cus_address or [EMPTY_ADDRESS.copy()]
            customer_pan = frappe.get_value('Customer', {'name': doc.customer_name}, ['pan'])

            cus_ship_link = frappe.get_all('Dynamic Link', filters={'link_doctype': 'Customer', 'link_name': doc['customer']}, fields=['parent'])
            cus_ship_address = frappe.get_all(
                'Address',
                filters={'name': cus_ship_link[0]['parent']} if cus_ship_link else {'name': '__missing__'},
                fields=['*']
            )
            cus_ship_address = cus_ship_address or [EMPTY_ADDRESS.copy()]

            cust_gstin = frappe.get_doc('Customer', doc.customer)

            gst_category = {
                "Unregistered": "Unregistered/Consumer",
                "Registered Regular": "Regular",
                "Registered Composition": "Composition",
                "SEZ": "Regular - SEZ"
            }.get(cust_gstin.gst_category, cust_gstin.gst_category)

            gl_entry = frappe.get_all('GL Entry', filters = {'voucher_no':doc.name}, fields = ['*'])
            gl_entry = gl_entry[::-1]


            for invoice in gl_entry:
                amount = invoice['credit'] if 'credit' in invoice and invoice['credit'] else invoice['debit']
                cr_dr = "Cr" if 'credit' in invoice and invoice['credit'] else "Dr"

                account_type = frappe.get_value('Account', invoice['account'], 'account_type')

                if account_type == 'Income Account':
                    ledgername = invoice['account']
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_suffix = None

                    sales_item = frappe.get_all('Sales Invoice Item', filters={'parent':doc.name}, fields=['*'])

                    for item in sales_item:
                        hsn_desc = frappe.get_value('GST HSN Code', {'name': item.get('gst_hsn_code')}, 'description')
                        gst_hsn_description = hsn_desc.replace('\n', ' ') if hsn_desc else ""

                        if item['sgst_rate']:
                            ledger_suffix = item['sgst_rate'] + item['cgst_rate']
                        elif item['gst_treatment'] == 'Exempted':
                            ledger_suffix = 'Exempt'
                        elif item['igst_rate']:
                            ledger_suffix = item['igst_rate']


                        ledger_dict = {
                            "Autoid": doc.name,
                            "CompanyNumber": str(company_id),
                            "TallyMasterid": 1,
                            "Voucherid": doc.name,
                            "VoucherNumber": doc.name,
                            "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                            "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                            "VoucherTypeParent": "Sales",
                            "LedgerName": f"{ledgername.split(' - ')[0]} @ {(ledger_suffix)}",
                            "LedgerParent": parent_account,

                            "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                            "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                            "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                            "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                            "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                            "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                            "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                            "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                            "BillName": doc.name,
                            "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                            "CrDr": cr_dr,
                            "CostCategory": get_tally_cost_category(doc),
                            "CostCentre": get_tally_cost_center(doc),
                            "Stockitem": item['item_code'],
                            "Godown": (item.get('warehouse') or default_warehouse).split('-')[0].strip() if (item.get('warehouse') or default_warehouse) else None,
                            "BatchNo": "",
                            "Quantity": item['qty'],
                            "Rate": item['net_rate'],
                            "Discount": "",
                            "Amount": item['taxable_value'],
                            "OrderNo": "",
                            "OrderDate": "",
                            "TrackingNo": "",
                            "TrackingDate": "",
                            "TermsOfPayment": "",
                            "OtherRef": "",
                            "TermsOfDelivery1": "",
                            "TermsOfDelivery2": "",
                            "DispatchDocNo": "",
                            "ReceiptDocNo": "",
                            "DispatchedThrough": "",
                            "Destination": "",
                            "CarrierName": "",
                            "BillOfLanding": "",
                            "BillOfLandingDate": "",
                            "VehicleNo": "",

                            "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                            "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                            "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                            "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                            "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                            "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                            "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                            "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                            "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                            "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                            "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                            "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',
    
                            "CmpGstRegistrationType":gst_category,
                            "CmpGstin":company_gst,
                            "CmpGstState":company_address[0]['state'] if company_address else "",
                            "GstOvrdnTaxability": "Taxable" if item.get('cgst_rate') else "Exempt",
                            "GstOvrdnTypeofsupply":"Goods",
                            "GstHsnName":item['gst_hsn_code'] if item['gst_hsn_code'] else "",
                            "GstHsnDescription": gst_hsn_description,
                            "CgstGstRateDutyhead":"CGST",
                            "CgstGstRateValuationtype":"Based on Value",
                            "CgstGstRate":item['cgst_rate'] if item['cgst_rate'] else "",
                            "SgstGstRateDutyhead":"SGST/UTGST",
                            "SgstGstRateValuationtype":"Based on Value",
                            "SgstGstRate":item['sgst_rate'] if item['sgst_rate'] else "",
                            "IgstGstRateDutyhead":"IGST",
                            "IgstGstRateValuationtype":"Based on Value",
                            "IgstGstRate": item['sgst_rate'] + item['cgst_rate'] if item['sgst_rate'] and item['cgst_rate'] else "",
                            "Narration": ""
                        }

                        all_vouchers.append(ledger_dict)
                    sales_item_processed = True  

                    if sales_item_processed:
                        continue

                # --------------------------------- The BELOW block of code is only for TAX ---------------------------------------------


                elif account_type == 'Tax':
                    
                    if not tax_processed:
                        ledgername = invoice['account']
                        parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')
                        tax_processed = True

                        items_tax = frappe.db.sql(f"""
                                    SELECT 
                                        parent,
                                        item_name,
                                        cgst_rate, 
                                        sgst_rate, 
                                        igst_rate,
                                        gst_treatment,
                                        SUM(cgst_amount) AS cgst_amount, 
                                        SUM(sgst_amount) AS sgst_amount, 
                                        SUM(igst_amount) AS igst_amount 
                                    FROM `tabSales Invoice Item` 
                                    WHERE parent='{doc.name}'
                                    GROUP BY parent, cgst_rate, sgst_rate, igst_rate
                                """, as_dict=True)

                        for item in items_tax:
                            if not item['gst_treatment'] == 'Exempted':
                                if item['cgst_rate']:
                                    ledger_dict = {
                                        "Autoid": doc.name,
                                        "CompanyNumber": str(company_id),
                                        "TallyMasterid": 1,
                                        "Voucherid": doc.name,
                                        "VoucherNumber": doc.name,
                                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                                        "VoucherTypeParent": "Sales",
                                        "LedgerName": f"Output Tax CGST @ {item['cgst_rate']}",
                                        "LedgerParent": parent_account,

                                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                                        "BillName": doc.name,
                                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "CrDr": cr_dr,
                                        "CostCategory": "",
                                        "CostCentre": "",
                                        "Stockitem": "",
                                        "Godown": "",
                                        "BatchNo": "",
                                        "Quantity": "",
                                        "Rate": "",
                                        "Discount": "",
                                        "Amount": round(item['cgst_amount'], 2),
                                        "OrderNo": "",
                                        "OrderDate": "",
                                        "TrackingNo": "",
                                        "TrackingDate": "",
                                        "TermsOfPayment": "",
                                        "OtherRef": "",
                                        "TermsOfDelivery1": "",
                                        "TermsOfDelivery2": "",
                                        "DispatchDocNo": "",
                                        "ReceiptDocNo": "",
                                        "DispatchedThrough": "",
                                        "Destination": "",
                                        "CarrierName": "",
                                        "BillOfLanding": "",
                                        "BillOfLandingDate": "",
                                        "VehicleNo": "",

                                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                        "CmpGstRegistrationType":gst_category,
                                        "CmpGstin":company_gst,
                                        "CmpGstState":company_address[0]['state'] if company_address else "",
                                        "GstOvrdnTaxability":"",
                                        "GstOvrdnTypeofsupply":"",
                                        "GstHsnName":"",
                                        "GstHsnDescription":"",
                                        "CgstGstRateDutyhead":"",
                                        "CgstGstRateValuationtype":"",
                                        "CgstGstRate":"",
                                        "SgstGstRateDutyhead":"",
                                        "SgstGstRateValuationtype":"",
                                        "SgstGstRate":"",
                                        "IgstGstRateDutyhead":"",
                                        "IgstGstRateValuationtype":"",
                                        "IgstGstRate":"",
                                        "Narration": ""
                                        }

                                    all_vouchers.append(ledger_dict)


                                if item['sgst_rate']:
                                    ledger_dict = {
                                        "Autoid": doc.name,
                                        "CompanyNumber": str(company_id),
                                        "TallyMasterid": 1,
                                        "Voucherid": doc.name,
                                        "VoucherNumber": doc.name,
                                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                                        "VoucherTypeParent": "Sales",
                                        "LedgerName": f"Output Tax SGST @ {item['cgst_rate']}",
                                        "LedgerParent": parent_account,

                                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                                        "BillName": doc.name,
                                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "CrDr": cr_dr,
                                        "CostCategory": "",
                                        "CostCentre": "",
                                        "Stockitem": "",
                                        "Godown": "",
                                        "BatchNo": "",
                                        "Quantity": "",
                                        "Rate": "",
                                        "Discount": "",
                                        "Amount": round(item['cgst_amount'], 2),
                                        "OrderNo": "",
                                        "OrderDate": "",
                                        "TrackingNo": "",
                                        "TrackingDate": "",
                                        "TermsOfPayment": "",
                                        "OtherRef": "",
                                        "TermsOfDelivery1": "",
                                        "TermsOfDelivery2": "",
                                        "DispatchDocNo": "",
                                        "ReceiptDocNo": "",
                                        "DispatchedThrough": "",
                                        "Destination": "",
                                        "CarrierName": "",
                                        "BillOfLanding": "",
                                        "BillOfLandingDate": "",
                                        "VehicleNo": "",

                                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                        "CmpGstRegistrationType":gst_category,
                                        "CmpGstin":company_gst,
                                        "CmpGstState":company_address[0]['state'] if company_address else "",
                                        "GstOvrdnTaxability":"",
                                        "GstOvrdnTypeofsupply":"",
                                        "GstHsnName":"",
                                        "GstHsnDescription":"",
                                        "CgstGstRateDutyhead":"",
                                        "CgstGstRateValuationtype":"",
                                        "CgstGstRate":"",
                                        "SgstGstRateDutyhead":"",
                                        "SgstGstRateValuationtype":"",
                                        "SgstGstRate":"",
                                        "IgstGstRateDutyhead":"",
                                        "IgstGstRateValuationtype":"",
                                        "IgstGstRate":"",
                                        "Narration": ""
                                        }

                                    all_vouchers.append(ledger_dict)

                                if item['igst_rate']:
                                    ledger_dict = {
                                        "Autoid": doc.name,
                                        "CompanyNumber": str(company_id),
                                        "TallyMasterid": 1,
                                        "Voucherid": doc.name,
                                        "VoucherNumber": doc.name,
                                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                                        "VoucherTypeParent": "Sales",
                                        "LedgerName": f"{ledgername.split(' - ')[0]} @ {item['igst_rate']}",
                                        "LedgerParent": parent_account,

                                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                                        "BillName": doc.name,
                                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                                        "CrDr": cr_dr,
                                        "CostCategory": "",
                                        "CostCentre": "",
                                        "Stockitem": "",
                                        "Godown": "",
                                        "BatchNo": "",
                                        "Quantity": "",
                                        "Rate": "",
                                        "Discount": "",
                                        "Amount": round(item['igst_amount'], 2),
                                        "OrderNo": "",
                                        "OrderDate": "",
                                        "TrackingNo": "",
                                        "TrackingDate": "",
                                        "TermsOfPayment": "",
                                        "OtherRef": "",
                                        "TermsOfDelivery1": "",
                                        "TermsOfDelivery2": "",
                                        "DispatchDocNo": "",
                                        "ReceiptDocNo": "",
                                        "DispatchedThrough": "",
                                        "Destination": "",
                                        "CarrierName": "",
                                        "BillOfLanding": "",
                                        "BillOfLandingDate": "",
                                        "VehicleNo": "",

                                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                                        "CmpGstRegistrationType":gst_category,
                                        "CmpGstin":company_gst,
                                        "CmpGstState":company_address[0]['state'] if company_address else "",
                                        "GstOvrdnTaxability":"",
                                        "GstOvrdnTypeofsupply":"",
                                        "GstHsnName":"",
                                        "GstHsnDescription":"",
                                        "CgstGstRateDutyhead":"",
                                        "CgstGstRateValuationtype":"",
                                        "CgstGstRate":"",
                                        "SgstGstRateDutyhead":"",
                                        "SgstGstRateValuationtype":"",
                                        "SgstGstRate":"",
                                        "IgstGstRateDutyhead":"",
                                        "IgstGstRateValuationtype":"",
                                        "IgstGstRate":"",
                                        "Narration": ""
                                        }

                                    all_vouchers.append(ledger_dict)
                    # --------------------------------- The ABOVE block of code is only for TAX ---------------------------------------------


                elif account_type == 'Bank':
                    ledgername = invoice['account']
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)


                elif account_type == 'Receivable':
                    ledgername = doc.customer
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": ledgername,
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": get_tally_cost_category(doc),
                        "CostCentre": get_tally_cost_center(doc),
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)


                # An expense is an expense whichever way the account is tagged:
                # Direct and Indirect Expense are the same thing to Tally as
                # Expense Account, and were previously dropped from the voucher.
                elif account_type in ("Expense Account", "Direct Expense", "Indirect Expense"):
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": invoice['account'].split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)


                elif account_type == "Chargeable":
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": invoice['account'].split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)


                elif account_type == "Cash":
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": invoice['account'].split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)
                    

                elif account_type == 'Round Off':
                    ledgername = 'Roundoff'
                    parent_account = frappe.get_value('Account', invoice['account'], 'custom_tally_parent_account')

                    ledger_dict = {
                        "Autoid": doc.name,
                        "CompanyNumber": str(company_id),
                        "TallyMasterid": 1,
                        "Voucherid": doc.name,
                        "VoucherNumber": doc.name,
                        "VoucherDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "VoucherType": "ERP POS Sales" if doc.is_pos else "ERP Sales",
                        "VoucherTypeParent": "Sales",
                        "LedgerName": ledgername.split(" - ")[0],
                        "LedgerParent": parent_account,

                        "LedgerAddress": cus_address[0]['city'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerState": cus_address[0]['state'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerCountry": cus_address[0]['country'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerPincode": cus_address[0]['pincode'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerMobile": cus_address[0]['phone'] if cus_address and parent_account== "Sundry Debtors" else "", 
                        "LedgerGstReg": gst_category if parent_account== "Sundry Debtors" else "", 
                        "LedgerPan": customer_pan if parent_account== "Sundry Debtors" else "", 
                        "LedgerGstin": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",

                        "BillName": doc.name,
                        "BillDate": datetime.strptime(str(doc.posting_date),'%Y-%m-%d').strftime('%d-%m-%Y'),
                        "CrDr": cr_dr,
                        "CostCategory": "",
                        "CostCentre": "",
                        "Stockitem": "",
                        "Godown": "",
                        "BatchNo": "",
                        "Quantity": "",
                        "Rate": "",
                        "Discount": "",
                        "Amount": round(amount, 2),
                        "OrderNo": "",
                        "OrderDate": "",
                        "TrackingNo": "",
                        "TrackingDate": "",
                        "TermsOfPayment": "",
                        "OtherRef": "",
                        "TermsOfDelivery1": "",
                        "TermsOfDelivery2": "",
                        "DispatchDocNo": "",
                        "ReceiptDocNo": "",
                        "DispatchedThrough": "",
                        "Destination": "",
                        "CarrierName": "",
                        "BillOfLanding": "",
                        "BillOfLandingDate": "",
                        "VehicleNo": "",

                        "BuyerName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerMailingName": doc.customer if parent_account== "Sundry Debtors" else "",
                        "BuyerAddress1": cus_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerAddress2": cus_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerState": cus_address[0]['state'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerCountry": cus_address[0]['country'] if parent_account== "Sundry Debtors" and cus_address else "",
                        "BuyerGstReg": gst_category if parent_account== "Sundry Debtors" else "",
                        "BuyerGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "BuyerPincode": cus_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_address else "",

                        "ConsigneeName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeMailingName": cus_ship_address[0]['address_title'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress1": cus_ship_address[0]['address_line1'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeAddress2": cus_ship_address[0]['address_line2'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeState": cus_ship_address[0]['state'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeCountry": cus_ship_address[0]['country'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "ConsigneeGSTIN": cust_gstin.gstin if parent_account== "Sundry Debtors" else "",
                        "ConsigneePincode": cus_ship_address[0]['pincode'] if parent_account== "Sundry Debtors" and cus_ship_address else "",
                        "PlaceOfSupply" : doc.place_of_supply.split("-", 1)[1] if parent_account== "Sundry Debtors" and doc.get('place_of_supply') and "-" in doc.place_of_supply else '',

                        "CmpGstRegistrationType":gst_category,
                        "CmpGstin":company_gst,
                        "CmpGstState":company_address[0]['state'] if company_address else "",
                        "GstOvrdnTaxability":"",
                        "GstOvrdnTypeofsupply":"",
                        "GstHsnName":"",
                        "GstHsnDescription":"",
                        "CgstGstRateDutyhead":"",
                        "CgstGstRateValuationtype":"",
                        "CgstGstRate":"",
                        "SgstGstRateDutyhead":"",
                        "SgstGstRateValuationtype":"",
                        "SgstGstRate":"",
                        "IgstGstRateDutyhead":"",
                        "IgstGstRateValuationtype":"",
                        "IgstGstRate":"",
                        "Narration": ""
                    }

                    all_vouchers.append(ledger_dict)

        final_voucher = ({
            "status": True,
            "VOUCHERDETAILS": {
                "VOUCHER": all_vouchers
            }
        })

        final_voucher = final_voucher
        final_voucher = Response(json.dumps(final_voucher, default=str), content_type='application/json')
        final_voucher.status_code = 200
        
        return final_voucher





@frappe.whitelist()
def fetch_response(response=None):
    data = json.loads(response) if isinstance(response, str) else response
    sales_response = data.get("SALES RESPONSE", [])

    for response in sales_response:
        sales_entry = response.get("AUTOID")
        guid = response.get("GUID")
        ref_no = response.get("REFNO")
        import_date = response.get("IMPORTDATE")
        import_time = response.get("IMPORTTIME")

        if not sales_entry:
            continue

        existing_sales = frappe.db.get_value("Sales Invoice", {"name": sales_entry}, "name")
        if existing_sales:
            import_date = datetime.strptime(import_date, "%Y%m%d").date()
            import_time = datetime.strptime(import_time, "%H:%M:%S").time()

            frappe.db.set_value("Sales Invoice", existing_sales, {
                "custom_tally_auto_id": sales_entry,
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
