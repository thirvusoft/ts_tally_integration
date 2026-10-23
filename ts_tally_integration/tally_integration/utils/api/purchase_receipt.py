import frappe
from ts_tally_integration.tally_integration.utils.api.sync_settings import company_filters, company_sync_required
from ts_tally_integration.tally_integration.utils.api.sync_settings import get_voucher_sync_limit
from datetime import datetime
import json
from werkzeug.wrappers import Response

def _get_address_details(address_doc):
    """Returns address fields as a dict, or blank if no doc."""
    if not address_doc:
        return dict(city="", state="", country="", pincode="", phone="", address_line1="", address_line2="", address_title="")
    doc = address_doc[0]
    return dict(
        city=doc.get("city", ""),
        state=doc.get("state", ""),
        country=doc.get("country", ""),
        pincode=doc.get("pincode", ""),
        phone=doc.get("phone", ""),
        address_line1=doc.get("address_line1", ""),
        address_line2=doc.get("address_line2", ""),
        address_title=doc.get("address_title", "")
    )



@frappe.whitelist()
@company_sync_required("VOUCHER")
def get_purchsase_receipt(company_id=None):
    if not company_id:
        return Response(json.dumps({'status': False, 'message': 'Company Number not found!'}), content_type='application/json')

    stock = frappe.get_value('TS Tally Company', company_filters(company_id), ['stock'])
    if stock == 'Non-Inventory':
        return Response(json.dumps({"status": True, "VOUCHERDETAILS": {"VOUCHER": []}}), content_type='application/json')

    company_name = frappe.get_value('TS Tally Company', company_filters(company_id), 'company_name')
    company_gst = frappe.get_value('Company', {'name': company_name}, 'gstin')

    all_vouchers = []

    delivery_notes = frappe.get_all('Purchase Receipt', filters={
        'company': company_name, 'is_return': 0, 'docstatus': 1
    }, fields=['*'], order_by='posting_date asc', limit=get_voucher_sync_limit())

    for doc in delivery_notes:
        # Customer address
        cus_address = frappe.get_all('Address', filters={'name': doc.supplier_address}, fields=['*']) if doc.supplier_address else []
        addr = _get_address_details(cus_address)
        customer_pan = frappe.get_value('Supplier', doc.supplier, 'pan')
        # Shipping address
        cus_ship_link = frappe.get_all('Dynamic Link', filters={'link_doctype': 'Supplier', 'link_name': doc['supplier']}, fields=['parent'])
        cus_ship_address = frappe.get_all('Address', filters={'name': cus_ship_link[0]['parent']} if cus_ship_link else {}, fields=['*'])
        ship_addr = _get_address_details(cus_ship_address)
        cust_doc = frappe.get_doc('Supplier', doc.supplier)
        gst_category = {
            "Unregistered": "Unregistered/Consumer",
            "Registered Regular": "Regular",
            "Registered Composition": "Composition",
            "SEZ": "Regular - SEZ"
        }.get(cust_doc.gst_category, cust_doc.gst_category or "")

        # 1. Customer ledger
        all_vouchers.append({
            "Autoid": doc.name,
            "CompanyNumber": str(company_id),
            "TallyMasterid": 1,
            "Voucherid": doc.name,
            "VoucherNumber": doc.name,
            "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
            "VoucherType": "ERP Purchase Receipt",
            "VoucherTypeParent": "Purchase Receipt",
            "LedgerName": doc.supplier,
            "LedgerParent": "Sundry Debtors",
            "LedgerAddress": addr['city'],
            "LedgerState": addr['state'],
            "LedgerCountry": addr['country'],
            "LedgerPincode": addr['pincode'],
            "LedgerMobile": addr['phone'],
            "LedgerGstReg": gst_category,
            "LedgerPan": customer_pan or "",
            "LedgerGstin": cust_doc.gstin or "",
            "BillName": doc.name,
            "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
            "CrDr": "Cr" if doc.rounded_total < 0 else "Dr",
            "CostCategory": "",
            "CostCentre": '',
            "Stockitem": '',
            "Godown": '',
            "BatchNo": '',
            "Quantity": '',
            "Rate": '',
            "Discount": "",
            "Amount": doc.rounded_total,

            "OrderNo": "",
            "OrderDate": "",
            "TrackingNo": "",
            "TrackingDate": "",
            "TermsOfPayment": "",
            "OtherRef": "",
            "TermsOfDelivery1": "",
            "TermsOfDelivery2": "",
            "DeliveryNoteNo1":"",
            "DeliveryNoteDate1":"",
            "DeliveryNoteNo2":"",
            "DeliveryNoteDate2":"",		
            "DeliveryNoteNo3":"",
            "DeliveryNoteDate3":"",
            "DeliveryNoteNo4":"",
            "DeliveryNoteDate4":"",
            "DeliveryNoteNo5":"",
            "DeliveryNoteDate5":"",		
            "DeliveryNoteNo6":"",
            "DeliveryNoteDate6":"",
            "DeliveryNoteNo7":"",
            "DeliveryNoteDate7":"",
            "DeliveryNoteNo8":"",
            "DeliveryNoteDate8":"",
            "DeliveryNoteNo9":"",
            "DeliveryNoteDate9":"",
            "DeliveryNoteNo10":"",
            "DeliveryNoteDate10":"",		
            "DispatchDocNo": "",
            "ReceiptDocNo": "",
            "DispatchedThrough": "",
            "Destination": "",
            "CarrierName": "",
            "BillOfLanding": "",
            "BillOfLandingDate": "",
            "VehicleNo": "",

            "BuyerName": doc.customer,
            "BuyerMailingName": doc.customer,
            "BuyerAddress1": addr['address_line1'],
            "BuyerAddress2": addr['address_line2'],
            "BuyerState": addr['state'],
            "BuyerCountry": addr['country'],
            "BuyerGstReg": gst_category,
            "BuyerGSTIN": cust_doc.gstin or "",
            "BuyerPincode": addr['pincode'],
            "ConsigneeName": ship_addr['address_title'],
            "ConsigneeMailingName": ship_addr['address_title'],
            "ConsigneeAddress1": ship_addr['address_line1'],
            "ConsigneeAddress2": ship_addr['address_line2'],
            "ConsigneeState": ship_addr['state'],
            "ConsigneeCountry": ship_addr['country'],
            "ConsigneeGSTIN": cust_doc.gstin or "",
            "ConsigneePincode": ship_addr['pincode'],
            "PlaceOfSupply": doc.place_of_supply,
            "Reference": "",
            "ReferenceDate": "",
            "Narration": ""
        })

        # 2. GST Ledgers (per item, for demo — you may need to aggregate instead of per row)
        items = frappe.get_all('Purchase Receipt Item', filters={'parent': doc.name}, fields=['*'])
        for item in items:
            # Only add tax ledgers if present
            if float(item.get('cgst_rate') or 0) > 0:

                all_vouchers.append({
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": "ERP Purchase Receipt",
                    "VoucherTypeParent": "Purchase Receipt",
                    "LedgerName": f"Input CGST @ {item['cgst_rate']}%",
                    "LedgerParent": "Duties & Taxes",
                    "LedgerAddress": "",
                    "LedgerState": "",
                    "LedgerCountry": "",
                    "LedgerPincode": "",
                    "LedgerMobile": "",
                    "LedgerGstReg": "",
                    "LedgerPan": "",
                    "LedgerGstin": "",
                    "BillName": doc.name,
                    "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": "Cr" if item.get("cgst_amount") < 0 else "Dr",
                    "CostCategory": "",
                    "CostCentre": '',
                    "Stockitem": item.get('item_code'),
                    "Godown": '',
                    "BatchNo": '',
                    "Quantity": item.get("qty"),
                    "Rate": "",
                    "Discount": "",
                    "Amount": item.get("cgst_amount"),
                    "OrderNo": "",

                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "3326",
                    "TermsOfDelivery2": "29-10-2022",
                    "DeliveryNoteNo1":"66",
                    "DeliveryNoteDate1":"29-10-2022",
                    "DeliveryNoteNo2":"67",
                    "DeliveryNoteDate2":"29-10-2022",		
                    "DeliveryNoteNo3":"",
                    "DeliveryNoteDate3":"",
                    "DeliveryNoteNo4":"",
                    "DeliveryNoteDate4":"",
                    "DeliveryNoteNo5":"",
                    "DeliveryNoteDate5":"",		
                    "DeliveryNoteNo6":"",
                    "DeliveryNoteDate6":"",
                    "DeliveryNoteNo7":"",
                    "DeliveryNoteDate7":"",
                    "DeliveryNoteNo8":"",
                    "DeliveryNoteDate8":"",
                    "DeliveryNoteNo9":"",
                    "DeliveryNoteDate9":"",
                    "DeliveryNoteNo10":"",
                    "DeliveryNoteDate10":"",		
                    "DispatchDocNo": "E4554654",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "Dfgdf5r5",
                    "Destination": "Ergry567",
                    "CarrierName": "567567ghgfh",
                    "BillOfLanding": "565656",
                    "BillOfLandingDate": "29-10-2022",
                    "VehicleNo": "",

                    "BuyerName": "",
                    "BuyerMailingName": "",
                    "BuyerAddress1": "",
                    "BuyerAddress2": "",
                    "BuyerState": "",
                    "BuyerCountry": "",
                    "BuyerGstReg": "",
                    "BuyerGSTIN": "",
                    "BuyerPincode": "",
                    "ConsigneeName": "",
                    "ConsigneeMailingName": "",
                    "ConsigneeAddress1": "",
                    "ConsigneeAddress2": "",
                    "ConsigneeState": "",
                    "ConsigneeCountry": "",
                    "ConsigneeGSTIN": "",
                    "ConsigneePincode": "",
                    "PlaceOfSupply": doc.place_of_supply,
                    "Reference": "",
                    "ReferenceDate": "",
                    "Narration": ""
                })

            if float(item.get('sgst_rate') or 0) > 0:

                all_vouchers.append({
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": "ERP Purchase Receipt",
                    "VoucherTypeParent": "Purchase Receipt",
                    "LedgerName":  f"Input SGST @ {item.get('sgst_rate')}%",
                    "LedgerParent": "Duties & Taxes",
                    "LedgerAddress": "",
                    "LedgerState": "",
                    "LedgerCountry": "",
                    "LedgerPincode": "",
                    "LedgerMobile": "",
                    "LedgerGstReg": "",
                    "LedgerPan": "",
                    "LedgerGstin": "",
                    "BillName": doc.name,
                    "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": "Cr" if item.get("cgst_amount") < 0 else "Dr",
                    "CostCategory": "",
                    "CostCentre": '',
                    "Stockitem": item.get('item_code'),
                    "Godown": '',
                    "BatchNo": '',
                    "Quantity": item.get("qty"),
                    "Rate": "",
                    "Discount": "",
                    "Amount": item.get("sgst_amount"),
                    "OrderNo": "",

                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "",
                    "TermsOfDelivery2": "",
                    "DeliveryNoteNo1":"",
                    "DeliveryNoteDate1":"",
                    "DeliveryNoteNo2":"",
                    "DeliveryNoteDate2":"",		
                    "DeliveryNoteNo3":"",
                    "DeliveryNoteDate3":"",
                    "DeliveryNoteNo4":"",
                    "DeliveryNoteDate4":"",
                    "DeliveryNoteNo5":"",
                    "DeliveryNoteDate5":"",		
                    "DeliveryNoteNo6":"",
                    "DeliveryNoteDate6":"",
                    "DeliveryNoteNo7":"",
                    "DeliveryNoteDate7":"",
                    "DeliveryNoteNo8":"",
                    "DeliveryNoteDate8":"",
                    "DeliveryNoteNo9":"",
                    "DeliveryNoteDate9":"",
                    "DeliveryNoteNo10":"",
                    "DeliveryNoteDate10":"",		
                    "DispatchDocNo": "",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "",
                    "Destination": "",
                    "CarrierName": "",
                    "BillOfLanding": "",
                    "BillOfLandingDate": "",
                    "VehicleNo": "",

                    "BuyerName": "",
                    "BuyerMailingName": "",
                    "BuyerAddress1": "",
                    "BuyerAddress2": "",
                    "BuyerState": "",
                    "BuyerCountry": "",
                    "BuyerGstReg": "",
                    "BuyerGSTIN": "",
                    "BuyerPincode": "",
                    "ConsigneeName": "",
                    "ConsigneeMailingName": "",
                    "ConsigneeAddress1": "",
                    "ConsigneeAddress2": "",
                    "ConsigneeState": "",
                    "ConsigneeCountry": "",
                    "ConsigneeGSTIN": "",
                    "ConsigneePincode": "",
                    "PlaceOfSupply": doc.place_of_supply,
                    "Reference": "",
                    "ReferenceDate": "",
                    "Narration": ""
                })


            if float(item.get('igst_rate') or 0) > 0:

                all_vouchers.append({
                    "Autoid": doc.name,
                    "CompanyNumber": str(company_id),
                    "TallyMasterid": 1,
                    "Voucherid": doc.name,
                    "VoucherNumber": doc.name,
                    "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "VoucherType": "ERP Purchase Receipt",
                    "VoucherTypeParent": "Purchase Receipt",
                    "LedgerName": f"Input IGST @ {item['igst_rate']}%",
                    "LedgerParent": "Duties & Taxes",
                    "LedgerAddress": "",
                    "LedgerState": "",
                    "LedgerCountry": "",
                    "LedgerPincode": "",
                    "LedgerMobile": "",
                    "LedgerGstReg": "",
                    "LedgerPan": "",
                    "LedgerGstin": "",
                    "BillName": doc.name,
                    "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                    "CrDr": "Cr" if item.get("cgst_amount") < 0 else "Dr",
                    "CostCategory": "",
                    "CostCentre": '',
                    "Stockitem": "",
                    "Godown": '',
                    "BatchNo": '',
                    "Quantity": "",
                    "Rate": "",
                    "Discount": "",
                    "Amount": item.get("igst_amount"),
                    "OrderNo": "",

                    "OrderDate": "",
                    "TrackingNo": "",
                    "TrackingDate": "",
                    "TermsOfPayment": "",
                    "OtherRef": "",
                    "TermsOfDelivery1": "",
                    "TermsOfDelivery2": "",
                    "DeliveryNoteNo1":"",
                    "DeliveryNoteDate1":"",
                    "DeliveryNoteNo2":"",
                    "DeliveryNoteDate2":"",		
                    "DeliveryNoteNo3":"",
                    "DeliveryNoteDate3":"",
                    "DeliveryNoteNo4":"",
                    "DeliveryNoteDate4":"",
                    "DeliveryNoteNo5":"",
                    "DeliveryNoteDate5":"",		
                    "DeliveryNoteNo6":"",
                    "DeliveryNoteDate6":"",
                    "DeliveryNoteNo7":"",
                    "DeliveryNoteDate7":"",
                    "DeliveryNoteNo8":"",
                    "DeliveryNoteDate8":"",
                    "DeliveryNoteNo9":"",
                    "DeliveryNoteDate9":"",
                    "DeliveryNoteNo10":"",
                    "DeliveryNoteDate10":"",		
                    "DispatchDocNo": "",
                    "ReceiptDocNo": "",
                    "DispatchedThrough": "",
                    "Destination": "",
                    "CarrierName": "",
                    "BillOfLanding": "",
                    "BillOfLandingDate": "",
                    "VehicleNo": "",

                    "BuyerName": "",
                    "BuyerMailingName": "",
                    "BuyerAddress1": "",
                    "BuyerAddress2": "",
                    "BuyerState": "",
                    "BuyerCountry": "",
                    "BuyerGstReg": "",
                    "BuyerGSTIN": "",
                    "BuyerPincode": "",
                    "ConsigneeName": "",
                    "ConsigneeMailingName": "",
                    "ConsigneeAddress1": "",
                    "ConsigneeAddress2": "",
                    "ConsigneeState": "",
                    "ConsigneeCountry": "",
                    "ConsigneeGSTIN": "",
                    "ConsigneePincode": "",
                    "PlaceOfSupply": doc.place_of_supply,
                    "Reference": "",
                    "ReferenceDate": "",
                    "Narration": ""
                })

        items = frappe.get_all('Purchase Receipt Item', filters={'parent': doc.name}, fields=['*'])
        for item in items:
            gst_percent = (item.get('cgst_rate') or 0) + (item.get('sgst_rate') or 0) + (item.get('igst_rate') or 0)
            ledger_name = f"Sales @ {gst_percent}%"

            all_vouchers.append({
                "Autoid": doc.name,
                "CompanyNumber": str(company_id),
                "TallyMasterid": 1,
                "Voucherid": doc.name,
                "VoucherNumber": doc.name,
                "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "VoucherType": "ERP Purchase Receipt",
                "VoucherTypeParent": "Purchase Receipt",
                "LedgerName": ledger_name,
                "LedgerParent": "Sales Accounts",
                "LedgerAddress": "",
                "LedgerState": "",
                "LedgerCountry": "",
                "LedgerPincode": "",
                "LedgerMobile": "",
                "LedgerGstReg": "",
                "LedgerPan": "",
                "LedgerGstin": "",
                "BillName": doc.name,
                "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "CrDr": "Cr" if item.get("cgst_amount") < 0 else "Dr",
                "CostCategory": "",
                "CostCentre": '',
                "Stockitem": item.get('item_code'),
                "Godown": '',
                "BatchNo": '',
                "Quantity": item.get("qty"),
                "Rate": "",
                "Discount": "",
                "Amount": item.get("cgst_amount"),
                "OrderNo": "",

                "OrderDate": "",
                "TrackingNo": "",
                "TrackingDate": "",
                "TermsOfPayment": "",
                "OtherRef": "",
                "TermsOfDelivery1": "",
                "TermsOfDelivery2": "",
                "DeliveryNoteNo1":"",
                "DeliveryNoteDate1":"",
                "DeliveryNoteNo2":"",
                "DeliveryNoteDate2":"",		
                "DeliveryNoteNo3":"",
                "DeliveryNoteDate3":"",
                "DeliveryNoteNo4":"",
                "DeliveryNoteDate4":"",
                "DeliveryNoteNo5":"",
                "DeliveryNoteDate5":"",		
                "DeliveryNoteNo6":"",
                "DeliveryNoteDate6":"",
                "DeliveryNoteNo7":"",
                "DeliveryNoteDate7":"",
                "DeliveryNoteNo8":"",
                "DeliveryNoteDate8":"",
                "DeliveryNoteNo9":"",
                "DeliveryNoteDate9":"",
                "DeliveryNoteNo10":"",
                "DeliveryNoteDate10":"",		
                "DispatchDocNo": "",
                "ReceiptDocNo": "",
                "DispatchedThrough": "",
                "Destination": "",
                "CarrierName": "",
                "BillOfLanding": "",
                "BillOfLandingDate": "",
                "VehicleNo": "",

                "BuyerName": "",
                "BuyerMailingName": "",
                "BuyerAddress1": "",
                "BuyerAddress2": "",
                "BuyerState": "",
                "BuyerCountry": "",
                "BuyerGstReg": "",
                "BuyerGSTIN": "",
                "BuyerPincode": "",
                "ConsigneeName": "",
                "ConsigneeMailingName": "",
                "ConsigneeAddress1": "",
                "ConsigneeAddress2": "",
                "ConsigneeState": "",
                "ConsigneeCountry": "",
                "ConsigneeGSTIN": "",
                "ConsigneePincode": "",
                "PlaceOfSupply": doc.place_of_supply,
                "Reference": "",
                "ReferenceDate": "",
                "Narration": ""
            })


        other_expenses = frappe.get_all('Sales Taxes and Charges', filters={'parent': doc.name, 'gst_tax_type': ['in', ['', None]]}, fields=['*'])
        for expense in other_expenses:
            parent_account = frappe.get_value('Account', expense.account_head, 'custom_tally_parent_account')

            all_vouchers.append({
                "Autoid": doc.name,
                "CompanyNumber": str(company_id),
                "TallyMasterid": 1,
                "Voucherid": doc.name,
                "VoucherNumber": doc.name,
                "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "VoucherType": "ERP Purchase Receipt",
                "VoucherTypeParent": "Purchase Receipt",
                "LedgerName": expense.description,
                "LedgerParent": parent_account,
                "LedgerAddress": "",
                "LedgerState": "",
                "LedgerCountry": "",
                "LedgerPincode": "",
                "LedgerMobile": "",
                "LedgerGstReg": "",
                "LedgerPan": "",
                "LedgerGstin": "",
                "BillName": doc.name,
                "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "CrDr":"Cr" if expense.base_tax_amount < 0 else "Dr",
                "CostCategory": "",
                "CostCentre": '',
                "Stockitem": "",
                "Godown": '',
                "BatchNo": '',
                "Quantity": "",
                "Rate": "",
                "Discount": "",
                "Amount": expense.base_tax_amount,
                "OrderNo": "",

                "OrderDate": "",
                "TrackingNo": "",
                "TrackingDate": "",
                "TermsOfPayment": "",
                "OtherRef": "",
                "TermsOfDelivery1": "",
                "TermsOfDelivery2": "",
                "DeliveryNoteNo1":"",
                "DeliveryNoteDate1":"",
                "DeliveryNoteNo2":"",
                "DeliveryNoteDate2":"",		
                "DeliveryNoteNo3":"",
                "DeliveryNoteDate3":"",
                "DeliveryNoteNo4":"",
                "DeliveryNoteDate4":"",
                "DeliveryNoteNo5":"",
                "DeliveryNoteDate5":"",		
                "DeliveryNoteNo6":"",
                "DeliveryNoteDate6":"",
                "DeliveryNoteNo7":"",
                "DeliveryNoteDate7":"",
                "DeliveryNoteNo8":"",
                "DeliveryNoteDate8":"",
                "DeliveryNoteNo9":"",
                "DeliveryNoteDate9":"",
                "DeliveryNoteNo10":"",
                "DeliveryNoteDate10":"",		
                "DispatchDocNo": "",
                "ReceiptDocNo": "",
                "DispatchedThrough": "",
                "Destination": "",
                "CarrierName": "",
                "BillOfLanding": "",
                "BillOfLandingDate": "",
                "VehicleNo": "",

                "BuyerName": "",
                "BuyerMailingName": "",
                "BuyerAddress1": "",
                "BuyerAddress2": "",
                "BuyerState": "",
                "BuyerCountry": "",
                "BuyerGstReg": "",
                "BuyerGSTIN": "",
                "BuyerPincode": "",
                "ConsigneeName": "",
                "ConsigneeMailingName": "",
                "ConsigneeAddress1": "",
                "ConsigneeAddress2": "",
                "ConsigneeState": "",
                "ConsigneeCountry": "",
                "ConsigneeGSTIN": "",
                "ConsigneePincode": "",
                "PlaceOfSupply": doc.place_of_supply,
                "Reference": "",
                "ReferenceDate": "",
                "Narration": ""
            })



        # 3. Round Off Ledger (if exists)
        if float(doc.get("rounding_adjustment") or 0):
            all_vouchers.append({
                "Autoid": doc.name,
                "CompanyNumber": str(company_id),
                "TallyMasterid": 1,
                "Voucherid": doc.name,
                "VoucherNumber": doc.name,
                "VoucherDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "VoucherType": "ERP Purchase Receipt",
                "VoucherTypeParent": "Purchase Receipt",
                "LedgerName": "Round Off",
                "LedgerParent": "Indirect Expenses",
                "LedgerAddress": "",
                "LedgerState": "",
                "LedgerCountry": "",
                "LedgerPincode": "",
                "LedgerMobile": "",
                "LedgerGstReg": "",
                "LedgerPan": "",
                "LedgerGstin": "",
                "BillName": doc.name,
                "BillDate": doc.posting_date.strftime('%d-%m-%Y') if isinstance(doc.posting_date, datetime) else datetime.strptime(str(doc.posting_date), '%Y-%m-%d').strftime('%d-%m-%Y'),
                "CrDr": "Cr" if doc.rounding_adjustment < 0 else "Dr",
                "CostCategory": "",
                "CostCentre": '',
                "Stockitem": '',
                "Godown": '',
                "BatchNo": '',
                "Quantity": "",
                "Rate": "",
                "Discount": "",
                "Amount": doc.rounding_adjustment,

                "OrderNo": "",
                "OrderDate": "",
                "TrackingNo": "",
                "TrackingDate": "",
                "TermsOfPayment": "",
                "OtherRef": "",
                "TermsOfDelivery1": "",
                "TermsOfDelivery2": "",
                "DeliveryNoteNo1":"",
                "DeliveryNoteDate1":"",
                "DeliveryNoteNo2":"",
                "DeliveryNoteDate2":"",		
                "DeliveryNoteNo3":"",
                "DeliveryNoteDate3":"",
                "DeliveryNoteNo4":"",
                "DeliveryNoteDate4":"",
                "DeliveryNoteNo5":"",
                "DeliveryNoteDate5":"",		
                "DeliveryNoteNo6":"",
                "DeliveryNoteDate6":"",
                "DeliveryNoteNo7":"",
                "DeliveryNoteDate7":"",
                "DeliveryNoteNo8":"",
                "DeliveryNoteDate8":"",
                "DeliveryNoteNo9":"",
                "DeliveryNoteDate9":"",
                "DeliveryNoteNo10":"",
                "DeliveryNoteDate10":"",		
                "DispatchDocNo": "",
                "ReceiptDocNo": "",
                "DispatchedThrough": "",
                "Destination": "",
                "CarrierName": "",
                "BillOfLanding": "",
                "BillOfLandingDate": "",
                "VehicleNo": "",

                "BuyerName": "",
                "BuyerMailingName": "",
                "BuyerAddress1": "",
                "BuyerAddress2": "",
                "BuyerState": "",
                "BuyerCountry": "",
                "BuyerGstReg": "",
                "BuyerGSTIN": "",
                "BuyerPincode": "",
                "ConsigneeName": "",
                "ConsigneeMailingName": "",
                "ConsigneeAddress1": "",
                "ConsigneeAddress2": "",
                "ConsigneeState": "",
                "ConsigneeCountry": "",
                "ConsigneeGSTIN": "",
                "ConsigneePincode": "",
                "PlaceOfSupply": doc.place_of_supply,
                "Reference": "",
                "ReferenceDate": "",
                "Narration": ""
            })

    response_json = {"status": True, "VOUCHERDETAILS": {"VOUCHER": all_vouchers}}
    return Response(json.dumps(response_json, default=str), content_type='application/json', status=200)





@frappe.whitelist()
def fetch_response(response):
    data = json.loads(response) if isinstance(response, str) else response
    purchasereceipt_response = data.get("PURCHASERECEIPT RESPONSE", [])

    for response in purchasereceipt_response:
        purchasereceipt_response = response.get("AUTOID")
        guid = response.get("GUID")
        ref_no = response.get("REFNO")
        import_date = response.get("IMPORTDATE")
        import_time = response.get("IMPORTTIME")

        if not purchasereceipt_response:
            continue

        existing_purchase = frappe.db.get_value("Purchase Receipt", {"name": purchasereceipt_response}, "name")
        if existing_purchase:
            import_date = datetime.strptime(import_date, "%Y%m%d").date()
            import_time = datetime.strptime(import_time, "%H:%M:%S").time()

            frappe.db.set_value("Purchase Receipt", existing_purchase, {
                "custom_tally_auto_id": existing_purchase,
                "custom_tally_guid": guid,
                "custom_tally_refno": ref_no,
                "custom_sync_time": datetime.combine(import_date, import_time)
            })

        else:
            frappe.log_error(f"Purchase Receipt not found for Tally AUTOID: {existing_purchase}", "Tally Purchase Receipt Sync Error")

    response =  {
        "status":True,
        "message":"Updated successfully"
        }
    return Response(json.dumps(response, default=str), content_type='application/json')
