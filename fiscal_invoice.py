#!/usr/bin/python
# -*- coding: utf-8 -*-
# This program is free software; you can redistribute it and/or modify
# it under the terms of the Affero GNU General Public License as published by
# the Software Foundation; either version 3, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTIBILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# Copyright 2013 by Mariano Reingart
# Based on code "openerp_pyafipws" 

"Fiscal Invoice for Argentina's Federal Tax Administration (AFIP) homologated printers"

__author__ = "Mariano Reingart (reingart@gmail.com)"
__copyright__ = "Copyright (C) 2013 Mariano Reingart and others"
__license__ = "AGPL 3.0+"


from osv import fields, osv
import os, time
import datetime
import decimal
import os
import socket
import sys
import traceback

DEBUG = True


class fiscal_invoice(osv.osv):
    _name = "account.invoice"
    _inherit = "account.invoice"
    _order = "id"
    _columns = {}
    _defaults = {}

    def action_pyfiscalprinter_print(self, cr, uid, ids, context=None, *args):
        "Print the invoice in an AFIP homologated printer"
        for invoice in self.browse(cr, uid, ids):
            # if already printed, ignore
            if invoice.state not in ('draft', 'proforma2'):
                continue
            # get the fiscal invoice type, point of sale and service:
            journal = invoice.journal_id
            company = journal.company_id
            ##tipo_cbte = journal.pyafipws_invoice_type
            ##punto_vta = journal.pyafipws_point_of_sale
            ##service = journal.pyafipws_electronic_invoice_service
            # check if it is an electronic invoice sale point:
            ##if not tipo_cbte or not punto_vta or not service:
            ##    continue

            # create the proxy and get the configuration system parameters:
            cfg = self.pool.get('ir.config_parameter')
            driver = cfg.get_param(cr, uid, 'pyfiscalprinter.driver', context=context) or "hasar"
            model = cfg.get_param(cr, uid, 'pyfiscalprinter.model', context=context) or None
            port = cfg.get_param(cr, uid, 'pyfiscalprinter.port', context=context) or "/dev/ttyS0"
            host = cfg.get_param(cr, uid, 'pyfiscalprinter.host', context=context)
            if host:
                port = int(port)
            dummy = False #cfg.get_param(cr, uid, 'pyfiscalprinter.dummy', context=context) != "false"

            if not model:
                continue
            
            # import the AFIP printer helper for fiscal invoice
            try:
                if driver == 'epson':
                    from pyfiscalprinter.epsonFiscal import EpsonPrinter
                    printer = EpsonPrinter(deviceFile=port, model=model, dummy=dummy)
                elif driver == 'hasar':
                    from pyfiscalprinter.hasarPrinter import HasarPrinter
                    printer = HasarPrinter(deviceFile=port, model=model, dummy=dummy, host=host, port=port)
                else:
                    raise osv.except_osv('Error !', "Unknown pyfiscalprinter driver: %s" % driver)
            except Exception, e:
                raise osv.except_osv('Error !', str(e).decode("latin1"))

            # get the last 8 digit of the invoice number
            cbte_nro = 0 # int(invoice.number[-8:])
            # get the last invoice number registered in AFIP printer
            cbte_nro_afip = printer.getLastNumber("B")
            cbte_nro_next = int(cbte_nro_afip or 0) + 1
            # verify that the invoice is the next one to be registered in AFIP    
            if False and cbte_nro != cbte_nro_next:
                raise osv.except_osv('Error !', 
                        'Referencia: %s \n' 
                        'El número del comprobante debería ser %s y no %s' % (
                        str(invoice.number), str(cbte_nro_next), str(cbte_nro)))

            # customer tax number:
            if invoice.partner_id.vat:
                nro_doc = invoice.partner_id.vat.replace("-","")
            else:
                nro_doc = "0"               # only "consumidor final"
            tipo_doc = None
            if nro_doc.startswith("AR"):
                nro_doc = nro_doc[2:]
                if int(nro_doc)  == 0:
                    tipo_doc = 99           # consumidor final
                elif len(nro_doc) < 11:
                    tipo_doc = 96           # DNI
                else:
                    tipo_doc = 80           # CUIT
            elif nro_doc.startswith("PA"):
                nro_doc = nro_doc[2:]

            # invoice amount totals:
            imp_total = abs(invoice.amount_total)
            imp_tot_conc = "0.00"
            imp_neto = abs(invoice.amount_untaxed)
            imp_iva = abs(invoice.amount_tax)
            imp_subtotal = imp_neto  # TODO: not allways the case!
            imp_trib = "0.00"
            imp_op_ex = "0.00"

            obs_generales = invoice.comment
            if invoice.payment_term:
                forma_pago = invoice.payment_term.name
                obs_comerciales = invoice.payment_term.name
            else:
                forma_pago = "Efectivo"
                obs_comerciales = None

            # customer data:
            nombre_cliente = invoice.partner_id.name

            # customer tax number:
            if invoice.partner_id.vat:
                nro_doc = invoice.partner_id.vat.replace("-","")
            else:
                nro_doc = "0"               # only "consumidor final"
            tipo_doc = 99
            if nro_doc.startswith("AR"):
                nro_doc = nro_doc[2:]
                if int(nro_doc)  == 0:
                    tipo_doc = DOC_TYPE_SIN_CALIFICADOR # consumidor final
                elif len(nro_doc) < 11:
                    tipo_doc = printer.DOC_TYPE_DNI
                else:
                    tipo_doc = printer.DOC_TYPE_CUIT
            if nro_doc.startswith("PA"):
                nro_doc = nro_doc[2:].encode("latin1")
                    
            
            # get the address
            if getattr(invoice, "address_invoice_id", None):
                address = invoice.address_invoice_id
            else:
                address = invoice.partner_id  # V7
            if address:
                domicilio_cliente = " - ".join([
                                    address.street or '',
                                    address.street2 or '',
                                    address.zip or '',
                                    address.city or '',
                                    ])
            else:
                domicilio_cliente = ""

            # customer VAT category ("Tipo de responsable" according RG1361)
            categoria = None
            if invoice.fiscal_position:
                if "monotributo" in invoice.fiscal_position.name.lower():
                    categoria = printer.IVA_TYPE_RESPONSABLE_MONOTRIBUTO
                elif "consumidor final" in invoice.fiscal_position.name.lower():
                    categoria = printer.IVA_TYPE_CONSUMIDOR_FINAL
                elif "exento" in invoice.fiscal_position.name.lower():
                    categoria = printer.IVA_TYPE_EXENTO
                elif "responsable inscripto" in invoice.fiscal_position.name.lower():
                    categoria = printer.IVA_TYPE_RESPONSABLE_INSCRIPTO

            # get the fiscal invoice type:
            if 'factura a' in journal.name.lower():
                tipo_cbte = "A"
            elif 'factura b' in journal.name.lower():
                tipo_cbte = "B"
            elif 'factura c' in journal.name.lower():
                tipo_cbte = "C"
            elif 'diario de ventas' in journal.name.lower() or \
                 'diario de abono de ventas' in journal.name.lower():
                tipo_cbte = "A" # para factura (panamá)
            else:
                raise osv.except_osv('Error !', 'tipo de comprobante invalido')
                tipo_cbte = None # just a fiscal ticket (without customer data)

            try:
                # start to print the invoice:
                printer.cancelAnyDocument()
                if not tipo_cbte:
                    printer.openTicket()
                elif invoice.type != "out_refund":
                    # abrir comprobante fiscal (factura):
                    printer.openBillTicket(tipo_cbte, nombre_cliente, 
                                           domicilio_cliente, nro_doc, tipo_doc, 
                                           categoria)
                else:
                    # abrir comprobante fiscal (nota de credito):
                    reference = invoice.origin
                    try:
                        nro_fac, nro_reg, fecha_fac, hora_fac = reference.split(" ")
                        fecha_fac = fecha_fac.replace("/", "-")
                        if "-" in fecha_fac: # convierto de DD-MM-AAAA al formato hasar
                            dia, mes, anio = [int(h) for h in fecha_fac.split("-")]
                            fecha_fac = "%02d%02d%02d" % (anio % 2000, mes, dia)
                        if ":" in hora_fac:
                            hora_fac = "%02d:%02d:%02d" % tuple([int(h) for h in hora_fac.split(":")])
                        reference = (nro_fac, nro_reg, fecha_fac, hora_fac)
                    except Exception, e:
                        raise osv.except_osv( 
                                'Error en Documento origen: %s!' % reference,
                                'Campos (separado por espacio): %s\nEjemplo: %s\n%s' % (
                                "nro factura, nro reg., fecha factura (DD-MM-AAAA), hora factura (HH:MM:SS)",
                                "00000341 1FHS110000713 03-02-2013 12:13:00",
                                str(e)))
                    printer.openBillCreditTicket(tipo_cbte, nombre_cliente, 
                                           domicilio_cliente, nro_doc, tipo_doc, 
                                           categoria, reference)
                         
                # print sample message
                ##printer.printNonFiscalText("generado desde openerp!")

                # print line items - invoice detail
                for line in invoice.invoice_line:
                    codigo = line.product_id.code
                    u_mtx = 1                       # TODO: get it from uom? 
                    cod_mtx = line.product_id.ean13
                    ds = line.name
                    qty = line.quantity
                    umed = 7                        # TODO: line.uos_id...?
                    price = line.price_unit
                    importe = line.price_subtotal
                    discount = line.discount or 0
                    # VAT:
                    if len(line.invoice_line_tax_id):
                        iva = (line.invoice_line_tax_id[0].amount) * 100.
                    else:
                        iva = 0
                    ##price = round(price * (100 + iva)/100., 2)  # add tax amount
                    printer.addItem(ds, qty, price, iva, discount, discountDescription="")

                
                # Send the payment terms           
                printer.addPayment(forma_pago, imp_total)

                # Send additional data
                ##if obs_generales:
                ##    printer.printNonFiscalText(obs_generales)
                ##if obs_comerciales:
                ##    printer.printNonFiscalText(obs_comerciales)

                # close the invoice
                ret = printer.closeDocument()        
                self.log(cr, uid, invoice.id, str(ret))

            except Exception, e:
                raise osv.except_osv('Error !', str(e).decode("latin1"))

            #raise osv.except_osv('Error !', 'Prueba OK')
                        
#    def unlink(self, cr, uid, ids, context=None):
#        if context is None:
#            context = {}
#        invoices = self.read(cr, uid, ids, ['state','internal_number'], context=context)
#        unlink_ids = []
#        for t in invoices:
#            if t['state'] in ('draft', 'cancel') and t['internal_number']== False:
#                unlink_ids.append(t['id'])
#            else:
#                raise osv.except_osv(_('Invalid Action!'), _('You can not delete an invoice which is not cancelled. You should refund it instead.'))
#        osv.osv.unlink(self, cr, uid, unlink_ids, context=context)
#        return True
    
fiscal_invoice()

