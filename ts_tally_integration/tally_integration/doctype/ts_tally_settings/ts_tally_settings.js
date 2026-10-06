// Copyright (c) 2024, Siddarth and contributors
// For license information, please see license.txt

frappe.ui.form.on('TS Tally Settings', {
    refresh: function (frm) {
        lock_voucher_sync_control(frm);
        set_child_queries(frm);
        fetch_unmapped_accounts(frm);
        fetch_sync_dashboard(frm);
        if (frm.doc.not_synced_company) {
            set_cost_center_for_company(frm);
            fetch_not_synced_data(frm);
        }
    },
    dashboard_from_date: function (frm) {
        refetch_dashboard_if_valid(frm);
    },
    dashboard_to_date: function (frm) {
        refetch_dashboard_if_valid(frm);
    },
    not_synced_company: function (frm) {
        if (frm.doc.not_synced_company) {
            set_cost_center_for_company(frm);
            fetch_not_synced_data(frm);
        } else {
            frm.set_value('not_synced_cost_center', '');
            frm.fields_dict['not_synced_data_html'].html('');
        }
    }
});

function set_cost_center_for_company(frm) {
    const row = (frm.doc.company_table || []).find(
        r => r.company_name === frm.doc.not_synced_company
    );
    frm.set_value('not_synced_cost_center', (row && row.cost_center) || '');
}

frappe.ui.form.on('TS Tally Company', {
    company_name: function (frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, 'cost_center', '');
    }
});

function set_child_queries(frm) {
    frm.set_query('cost_center', 'company_table', function (doc, cdt, cdn) {
        const row = locals[cdt][cdn];

        return {
            filters: {
                company: row.company_name || ''
            }
        };
    });
}


function fetch_unmapped_accounts(frm) {
    if (frm.fields_dict['unmapped_accounts']) {
        frappe.call({
            method: 'ts_tally_integration.tally_integration.doctype.ts_tally_settings.ts_tally_settings.get_unmapped_accounts',
            callback: function (r) {
                if (r.message && r.message.length > 0) {
                    let table = `
                        <h3 style="text-align: center">UNMAPPED ACCOUNTS</h3>
                        <table style="width: 100%; border-collapse: collapse;">
                            <th>
                                <tr>
                                    <th style="border: 1px solid #ddd; padding: 8px; background:rgb(183, 220, 255);">S.No</th>
                                    <th style="border: 1px solid #ddd; padding: 8px; background:rgb(183, 220, 255);">Account Name</th>
                                    <th style="border: 1px solid #ddd; padding: 8px; background:rgb(183, 220, 255);">Company</th>
                                </tr>
                            </th>
                    `;

                    r.message.forEach((account, index) => {
                        table += `
                            <tr>
                                <td style="border: 1px solid #ddd; padding: 8px;">${index + 1}</td>
                                <td style="border: 1px solid #ddd; padding: 8px;">${account.name}</td>
                                <td style="border: 1px solid #ddd; padding: 8px;">${account.company}</td>
                            </tr>
                        `;
                    });


                    frm.fields_dict['unmapped_accounts'].html(table);
                } else {
                    frm.fields_dict['unmapped_accounts'].html("<h3>ALL ACCOUNTS MAPPED</h3>");
                }
            }
        });
    }
}


function fetch_not_synced_data(frm) {
    if (!frm.fields_dict['not_synced_data_html']) return;

    frappe.call({
        method: 'ts_tally_integration.tally_integration.doctype.ts_tally_settings.ts_tally_settings.get_not_synced_data',
        args: { company: frm.doc.not_synced_company },
        freeze: true,
        freeze_message: 'Fetching not synced data...',
        callback: function (r) {
            if (!r.message || Object.keys(r.message).length === 0) {
                frm.fields_dict['not_synced_data_html'].html(
                    '<div style="text-align:center; padding:20px;"><h3>All Data is Synced</h3></div>'
                );
                return;
            }

            let html = '';
            for (let doctype in r.message) {
                let records = r.message[doctype];
                let columns = Object.keys(records[0]);

                html += `
                    <div style="margin-bottom:20px;">
                        <h4 style="background:rgb(183, 220, 255); padding:8px; margin-bottom:0;">
                            ${doctype} (${records.length} records)
                        </h4>
                        <table class="table table-bordered" style="width:100%; border-collapse:collapse;">
                            <thead>
                                <tr>
                                    <th style="border:1px solid #ddd; padding:8px;">S.No</th>
                                    ${columns.map(col => `<th style="border:1px solid #ddd; padding:8px;">${frappe.model.unscrub(col)}</th>`).join('')}
                                </tr>
                            </thead>
                            <tbody>
                `;

                const date_cols = new Set(['creation', 'modified', 'posting_date']);
                records.forEach((row, idx) => {
                    html += '<tr>';
                    html += `<td style="border:1px solid #ddd; padding:8px;">${idx + 1}</td>`;
                    columns.forEach(col => {
                        let val = row[col] || '';
                        if (col === 'name') {
                            val = `<a href="/app/${frappe.router.slug(doctype)}/${row[col]}" target="_blank">${row[col]}</a>`;
                        } else if (date_cols.has(col) && row[col]) {
                            val = frappe.datetime.str_to_user(row[col]);
                        }
                        html += `<td style="border:1px solid #ddd; padding:8px;">${val}</td>`;
                    });
                    html += '</tr>';
                });

                html += `
                            </tbody>
                        </table>
                    </div>
                `;
            }

            frm.fields_dict['not_synced_data_html'].html(html);
        }
    });
}


function refetch_dashboard_if_valid(frm) {
    const { dashboard_from_date: from_date, dashboard_to_date: to_date } = frm.doc;
    if (from_date && to_date && from_date > to_date) {
        frappe.msgprint(__('From Date must be on or before To Date.'));
        return;
    }
    fetch_sync_dashboard(frm);
}

function fetch_sync_dashboard(frm) {
    const field = frm.fields_dict['sync_dashboard_html'];
    if (!field) return;
    const from_date = frm.doc.dashboard_from_date || null;
    const to_date = frm.doc.dashboard_to_date || null;

    field.html(
        '<div style="text-align:center; padding:20px; color:#888;">Loading sync dashboard...</div>'
    );

    frappe.call({
        method: 'ts_tally_integration.tally_integration.doctype.ts_tally_settings.ts_tally_settings.get_sync_dashboard_data',
        args: { from_date, to_date },
        callback: function (r) {
            if (!r.message) return;
            field.html(render_sync_dashboard(r.message, { from_date, to_date }));
            field.$wrapper.off('.syncdash').on('click.syncdash', '[data-action="sd-refresh"]', () =>
                fetch_sync_dashboard(frm)
            );
        }
    });
}

function render_sync_dashboard(data, range) {
    range = range || {};
    const active = range.from_date || range.to_date;
    const summary = active
        ? `Showing ${range.from_date ? 'from <b>' + frappe.datetime.str_to_user(range.from_date) + '</b>' : ''}` +
          `${range.to_date ? ' to <b>' + frappe.datetime.str_to_user(range.to_date) + '</b>' : ''}` +
          ` — vouchers by Posting Date, masters by Created On.`
        : 'Showing all dates.';

    let html = `
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
            <span style="font-size:12px; color:#666;">${summary}</span>
            <button class="btn btn-default btn-sm" data-action="sd-refresh">Refresh</button>
        </div>
    `;

    if (data.global_masters && data.global_masters.length) {
        html += render_dashboard_section('Global Masters (Company Independent)', data.global_masters);
    }

    (data.companies || []).forEach(c => {
        const meta = [];
        if (c.sync_from) meta.push(`Sync From: <b>${frappe.datetime.str_to_user(c.sync_from)}</b>`);
        if (c.cost_center) meta.push(`Cost Center: <b>${frappe.utils.escape_html(c.cost_center)}</b>`);
        const meta_html = meta.length
            ? `<div style="margin:4px 0 8px; color:#666; font-size:12px;">${meta.join(' &nbsp;|&nbsp; ')}</div>`
            : '';

        html += `
            <div style="margin-top:24px; padding:12px; border:1px solid #e2e6ea; border-radius:6px;">
                <h3 style="margin:0;">${frappe.utils.escape_html(c.company)}</h3>
                ${meta_html}
        `;
        if (c.masters && c.masters.length) {
            html += render_dashboard_section('Master Data', c.masters);
        }
        if (c.vouchers && c.vouchers.length) {
            html += render_dashboard_section('Voucher Data', c.vouchers);
        }
        html += `</div>`;
    });

    return html;
}

function render_dashboard_section(title, rows) {
    let body = '';
    rows.forEach(r => {
        const total = (r.pending || 0) + (r.synced || 0);
        const pending_color = (r.pending && r.pending > 0) ? '#c0392b' : '#2c3e50';
        const last_sync = r.last_sync ? frappe.datetime.str_to_user(r.last_sync) : '—';

        body += `
            <tr>
                <td style="border:1px solid #ddd; padding:8px;">${frappe.utils.escape_html(r.doctype)}</td>
                <td style="border:1px solid #ddd; padding:8px; text-align:right; color:${pending_color}; font-weight:600;">${r.pending || 0}</td>
                <td style="border:1px solid #ddd; padding:8px; text-align:right; color:#27ae60; font-weight:600;">${r.synced || 0}</td>
                <td style="border:1px solid #ddd; padding:8px; text-align:right; color:#666;">${total}</td>
                <td style="border:1px solid #ddd; padding:8px;">${last_sync}</td>
            </tr>
        `;
    });

    return `
        <h4 style="background:rgb(183, 220, 255); padding:8px; margin: 12px 0 0;">${title}</h4>
        <table class="table table-bordered" style="width:100%; border-collapse:collapse; margin:0;">
            <thead>
                <tr>
                    <th style="border:1px solid #ddd; padding:8px; text-align:left;">Doctype</th>
                    <th style="border:1px solid #ddd; padding:8px; text-align:right;">Pending</th>
                    <th style="border:1px solid #ddd; padding:8px; text-align:right;">Synced</th>
                    <th style="border:1px solid #ddd; padding:8px; text-align:right;">Total</th>
                    <th style="border:1px solid #ddd; padding:8px; text-align:left;">Last Sync</th>
                </tr>
            </thead>
            <tbody>${body}</tbody>
        </table>
    `;
}


// Voucher Sync Control rows are created on migrate (append_voucher_sync_control),
// one per voucher type. Users only change the settings on each row; adding or
// deleting rows here would leave the list out of step with the vouchers synced.
function lock_voucher_sync_control(frm) {
    const grid = frm.fields_dict.voucher_sync_control && frm.fields_dict.voucher_sync_control.grid;
    if (!grid) return;
    grid.cannot_add_rows = true;
    grid.df.cannot_add_rows = true;
    grid.df.cannot_delete_rows = true;
    grid.refresh();
}
