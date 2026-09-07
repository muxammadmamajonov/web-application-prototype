"""ORM model package.

Importing this module pulls every model into ``app.db.Base.metadata`` so
Alembic autogenerate sees the full schema in dependency order.
"""
from __future__ import annotations

from .audit import AuditLog
from .billing import Invoice, SequenceCounter, WebhookDLQ
from .broadcast import Broadcast, BroadcastRecipient
from .catalog import CatalogBrand, CatalogCategory, CatalogColor, CatalogModel
from .consent import ConsentRecord
from .cron import CronLock
from .customer import Customer
from .document_delivery import DocumentDelivery
from .guarantor import Guarantor, InstallmentGuarantor
from .holiday import Holiday
from .installment import Installment, InstallmentItem, InstallmentItemIdentifier
from .invitation import Invitation
from .invitation import TenantRole as TenantCustomRole
from .media import MediaFile
from .messaging import NotificationChannel, SmsMessage
from .notification import Notification
from .payment import KycBlob, Payment, ReceiptBlob, TenantPaymentProvider
from .payment_review_notice import PaymentReviewNotice
from .product import Product
from .promotion import AdCampaign, PromoCode, Referral
from .risk import CustomerRiskSignal
from .settings import PlatformSetting, TenantSettings
from .stock import StockMovement
from .tenant import Plan, Tenant, TenantBalanceTxn, TenantSubscription, TenantTopup
from .user import TenantUser, User

__all__ = (
    "AdCampaign",
    "AuditLog",
    "Broadcast",
    "BroadcastRecipient",
    "CatalogBrand",
    "CatalogCategory",
    "CatalogColor",
    "CatalogModel",
    "ConsentRecord",
    "CronLock",
    "Customer",
    "CustomerRiskSignal",
    "DocumentDelivery",
    "Guarantor",
    "Holiday",
    "Installment",
    "InstallmentGuarantor",
    "InstallmentItem",
    "InstallmentItemIdentifier",
    "Invitation",
    "Invoice",
    "KycBlob",
    "MediaFile",
    "Notification",
    "NotificationChannel",
    "Payment",
    "PaymentReviewNotice",
    "Plan",
    "PlatformSetting",
    "Product",
    "PromoCode",
    "ReceiptBlob",
    "Referral",
    "SequenceCounter",
    "SmsMessage",
    "StockMovement",
    "Tenant",
    "TenantBalanceTxn",
    "TenantCustomRole",
    "TenantPaymentProvider",
    "TenantSettings",
    "TenantSubscription",
    "TenantTopup",
    "TenantUser",
    "User",
    "WebhookDLQ",
)
