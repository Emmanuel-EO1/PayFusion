import logging
import json
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Prefetch, Sum, Count
from django.views.decorators.http import require_POST

from tenants.models import Business, WithdrawalRequest
from orders.models import Order, OrderItem
from transactions.models import Transaction
from users.models import UserProfile, DeliveryAddress, WishlistItem

logger = logging.getLogger('payfusion')
User = get_user_model()


class RememberMeLoginView(DjangoLoginView):
    def form_valid(self, form):
        remember_me = self.request.POST.get('remember_me')
        if not remember_me:
            self.request.session.set_expiry(0)
        else:
            self.request.session.set_expiry(2592000)
        return super().form_valid(form)

# ============================================================
# REGISTRATION
# ============================================================

def register(request):
    if request.user.is_authenticated:
        return redirect('core:home')

    if request.method == 'GET':
        return render(request, 'users/register.html')

    email = request.POST.get('email', '').strip().lower()
    username = request.POST.get('username', '').strip()
    password1 = request.POST.get('password1', '')
    password2 = request.POST.get('password2', '')

    errors = []

    if not email:
        errors.append('Email address is required.')
    elif User.objects.filter(email=email).exists():
        errors.append('An account with this email already exists.')

    if not username:
        errors.append('Username is required.')
    elif User.objects.filter(username=username).exists():
        errors.append('This username is already taken.')

    if not password1:
        errors.append('Password is required.')
    elif len(password1) < 8:
        errors.append('Password must be at least 8 characters.')
    elif password1 != password2:
        errors.append('Passwords do not match.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'users/register.html', {
            'email': email,
            'username': username,
        })

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password1,
    )

    _send_verification_email(user)

    messages.success(
        request,
        'Account created. Please check your email to verify your account '
        'before logging in.'
    )
    return redirect('users:login')


def _send_verification_email(user):
    """
    Generates a signed verification token and emails the user
    a link to verify their email address.
    Uses Django's signing module — token is tamper-proof and
    expires after 7 days.
    """
    from django.core.mail import send_mail
    from django.conf import settings

    token = signing.dumps(user.pk, salt='email-verification')

    verify_url = (
        f'{settings.SITE_URL}/users/verify-email/{token}/'
    )

    send_mail(
        subject='Verify your PayFusion email address',
        message=(
            f'Hi {user.username},\n\n'
            f'Please verify your email address by clicking the link below:\n\n'
            f'{verify_url}\n\n'
            f'This link expires in 7 days.\n\n'
            f'If you did not create a PayFusion account, ignore this email.\n\n'
            f'— The PayFusion Team'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

    logger.info(f'Verification email sent to {user.email}')


def verify_email(request, token):
    """
    Verifies the email token from the link in the verification email.
    Marks the user's profile as verified and logs them in.
    Token expires after 7 days.
    """
    try:
        user_pk = signing.loads(
            token,
            salt='email-verification',
            max_age=60 * 60 * 24 * 7,  # 7 days in seconds
        )
    except signing.SignatureExpired:
        messages.error(
            request,
            'This verification link has expired. Please register again '
            'or contact support.'
        )
        return redirect('users:login')
    except signing.BadSignature:
        messages.error(request, 'Invalid verification link.')
        return redirect('users:login')

    user = get_object_or_404(User, pk=user_pk)

    if user.profile.is_email_verified:
        messages.info(request, 'Your email is already verified. Please log in.')
        return redirect('users:login')

    user.profile.is_email_verified = True
    user.profile.save(update_fields=['is_email_verified', 'updated_at'])

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    messages.success(
        request,
        'Email verified successfully. Welcome to PayFusion!'
    )
    return redirect('core:home')


# ============================================================
# CUSTOMER PROFILE
# ============================================================

@login_required
def customer_profile(request):
    profile = request.user.profile

    if request.method == 'GET':
        return render(request, 'users/customer_profile.html', {
            'profile': profile,
        })

    display_name = request.POST.get('display_name', '').strip()
    phone = request.POST.get('phone', '').strip()
    notify_order = request.POST.get('notify_order_updates') == 'on'
    notify_delivery = request.POST.get('notify_delivery_updates') == 'on'
    notify_dispute = request.POST.get('notify_dispute_updates') == 'on'
    notify_promotions = request.POST.get('notify_promotions') == 'on'

    if len(display_name) > 100:
        messages.error(request, 'Display name must be 100 characters or fewer.')
        return render(request, 'users/customer_profile.html', {'profile': profile})

    profile.display_name = display_name
    profile.phone = phone
    profile.notify_order_updates = notify_order
    profile.notify_delivery_updates = notify_delivery
    profile.notify_dispute_updates = notify_dispute
    profile.notify_promotions = notify_promotions
    profile.save()

    messages.success(request, 'Profile updated successfully.')
    return redirect('users:customer_profile')


# ============================================================
# DELIVERY ADDRESSES
# ============================================================

@login_required
def manage_addresses(request):
    addresses = request.user.delivery_addresses.all()
    return render(request, 'users/manage_addresses.html', {
        'addresses': addresses,
    })


@login_required
def add_address(request):
    if request.method == 'GET':
        return render(request, 'users/address_form.html', {
            'address': None,
        })

    full_name = request.POST.get('full_name', '').strip()
    phone = request.POST.get('phone', '').strip()
    address_line_1 = request.POST.get('address_line_1', '').strip()
    address_line_2 = request.POST.get('address_line_2', '').strip()
    city = request.POST.get('city', '').strip()
    state = request.POST.get('state', '').strip()
    country = request.POST.get('country', 'Nigeria').strip()
    is_default = request.POST.get('is_default') == 'on'

    errors = []
    if not full_name:
        errors.append('Full name is required.')
    if not phone:
        errors.append('Phone number is required.')
    if not address_line_1:
        errors.append('Address line 1 is required.')
    if not city:
        errors.append('City is required.')
    if not state:
        errors.append('State is required.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'users/address_form.html', {'address': None})

    DeliveryAddress.objects.create(
        user=request.user,
        full_name=full_name,
        phone=phone,
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        country=country,
        is_default=is_default,
    )

    messages.success(request, 'Address added successfully.')
    return redirect('users:manage_addresses')


@login_required
def edit_address(request, address_id):
    address = get_object_or_404(
        DeliveryAddress,
        id=address_id,
        user=request.user,
    )

    if request.method == 'GET':
        return render(request, 'users/address_form.html', {
            'address': address,
        })

    address.full_name = request.POST.get('full_name', '').strip()
    address.phone = request.POST.get('phone', '').strip()
    address.address_line_1 = request.POST.get('address_line_1', '').strip()
    address.address_line_2 = request.POST.get('address_line_2', '').strip()
    address.city = request.POST.get('city', '').strip()
    address.state = request.POST.get('state', '').strip()
    address.country = request.POST.get('country', 'Nigeria').strip()
    address.is_default = request.POST.get('is_default') == 'on'
    address.save()

    messages.success(request, 'Address updated.')
    return redirect('users:manage_addresses')


@require_POST
@login_required
def delete_address(request, address_id):
    address = get_object_or_404(
        DeliveryAddress,
        id=address_id,
        user=request.user,
    )
    address.delete()
    messages.success(request, 'Address removed.')
    return redirect('users:manage_addresses')


@require_POST
@login_required
def set_default_address(request, address_id):
    address = get_object_or_404(
        DeliveryAddress,
        id=address_id,
        user=request.user,
    )
    address.is_default = True
    address.save()
    messages.success(request, f'{address.city} set as your default address.')
    return redirect('users:manage_addresses')


# ============================================================
# WISHLIST
# ============================================================

@login_required
def wishlist(request):
    items = (
        WishlistItem.objects
        .filter(user=request.user)
        .select_related('product__business')
        .prefetch_related('product__tags')
        .order_by('-added_at')
    )
    return render(request, 'users/wishlist.html', {'items': items})


@require_POST
@login_required
def wishlist_add(request, product_id):
    from products.models import Product
    product = get_object_or_404(Product, id=product_id, is_active=True)

    _, created = WishlistItem.objects.get_or_create(
        user=request.user,
        product=product,
    )

    if created:
        messages.success(request, f'{product.name} added to your wishlist.')
    else:
        messages.info(request, f'{product.name} is already in your wishlist.')

    return redirect('products:product_detail', id=product_id)


@require_POST
@login_required
def wishlist_remove(request, product_id):
    WishlistItem.objects.filter(
        user=request.user,
        product_id=product_id,
    ).delete()

    messages.success(request, 'Item removed from wishlist.')

    next_url = request.POST.get('next', 'users:wishlist')
    if next_url.startswith('/'):
        return redirect(next_url)
    return redirect('users:wishlist')


# ============================================================
# VENDOR DASHBOARD
# ============================================================

@login_required
def dashboard(request):
    businesses = (
        Business.objects
        .filter(owner=request.user)
        .select_related('wallet')
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects.select_related(
                    'business'
                ).order_by('-created_at')
            )
        )
    )

    if not businesses.exists():
        return redirect('core:home')

    dashboard_data = []

    for business in businesses:
        orders = business.orders.all()

        total_revenue = orders.filter(status='paid').aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        total_orders = orders.aggregate(
            count=Count('id')
        )['count'] or 0

        pending_orders = orders.filter(status='pending').count()

        wallet = business.wallet if hasattr(business, 'wallet') else None

        dashboard_data.append({
            'business': business,
            'available_balance': wallet.balance if wallet else 0,
            'escrow_balance': wallet.escrow_balance if wallet else 0,
            'total_balance': wallet.total_balance if wallet else 0,
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'recent_orders': orders[:5],
        })

    return render(request, 'users/dashboard.html', {
        'dashboard_data': dashboard_data,
    })


@login_required
def transaction_history(request):
    transactions = (
        Transaction.objects
        .filter(
            user=request.user,
            transaction_type='payment',
        )
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects.select_related(
                    'business'
                ).prefetch_related('items__product')
            )
        )
        .order_by('-created_at')
    )

    return render(request, 'users/transaction_history.html', {
        'transactions': transactions,
    })


@login_required
def sales_history(request):
    businesses = (
        Business.objects
        .filter(owner=request.user)
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects
                    .select_related('user', 'transaction')
                    .prefetch_related('items__product')
                    .order_by('-created_at')
            )
        )
    )

    if not businesses.exists():
        return redirect('core:home')

    return render(request, 'users/sales_history.html', {
        'businesses': businesses,
    })


@login_required
def payout_history(request):
    businesses = Business.objects.filter(owner=request.user)

    if not businesses.exists():
        return redirect('core:home')

    withdrawals = (
        WithdrawalRequest.objects
        .filter(business__in=businesses)
        .select_related(
            'business',
            'bank_account',
            'transaction',
            'reviewed_by',
        )
        .order_by('-created_at')
    )

    return render(request, 'users/payout_history.html', {
        'withdrawals': withdrawals,
        'businesses': businesses,
    })


@login_required
def business_analytics(request, business_id):
    from django.db.models import Sum, Count
    from django.db.models.functions import TruncDay
    from django.utils import timezone

    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    thirty_days_ago = timezone.now() - timezone.timedelta(days=30)

    daily_revenue = (
        business.orders
        .filter(status='paid', created_at__gte=thirty_days_ago)
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(revenue=Sum('total_amount'))
        .order_by('day')
    )

    daily_orders = (
        business.orders
        .filter(status='paid', created_at__gte=thirty_days_ago)
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    top_by_revenue = (
        OrderItem.objects
        .filter(order__business=business, order__status='paid')
        .values('product__name')
        .annotate(revenue=Sum('price'))
        .order_by('-revenue')[:10]
    )

    top_by_units = (
        OrderItem.objects
        .filter(order__business=business, order__status='paid')
        .values('product__name')
        .annotate(units=Sum('quantity'))
        .order_by('-units')[:10]
    )

    revenue_labels = [entry['day'].strftime('%b %d') for entry in daily_revenue]
    revenue_data   = [float(entry['revenue']) for entry in daily_revenue]
    order_labels   = [entry['day'].strftime('%b %d') for entry in daily_orders]
    order_data     = [entry['count'] for entry in daily_orders]
    rev_product_labels  = [entry['product__name'] for entry in top_by_revenue]
    rev_product_data    = [float(entry['revenue']) for entry in top_by_revenue]
    unit_product_labels = [entry['product__name'] for entry in top_by_units]
    unit_product_data   = [entry['units'] for entry in top_by_units]

    return render(request, 'users/business_analytics.html', {
        'business': business,
        'revenue_labels':       json.dumps(revenue_labels),
        'revenue_data':         json.dumps(revenue_data),
        'order_labels':         json.dumps(order_labels),
        'order_data':           json.dumps(order_data),
        'rev_product_labels':   json.dumps(rev_product_labels),
        'rev_product_data':     json.dumps(rev_product_data),
        'unit_product_labels':  json.dumps(unit_product_labels),
        'unit_product_data':    json.dumps(unit_product_data),
    })


@login_required
def earnings_statement(request, business_id):
    from django.http import HttpResponse
    from django.utils import timezone
    from datetime import datetime
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    )

    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    if request.method == 'GET':
        return render(request, 'users/earnings_statement.html', {
            'business': business,
        })

    date_from_raw = request.POST.get('date_from', '').strip()
    date_to_raw   = request.POST.get('date_to', '').strip()

    try:
        date_from = datetime.strptime(date_from_raw, '%Y-%m-%d').date()
        date_to   = datetime.strptime(date_to_raw,   '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Please enter valid dates in YYYY-MM-DD format.')
        return render(request, 'users/earnings_statement.html', {'business': business})

    if date_from > date_to:
        messages.error(request, 'Start date must be before end date.')
        return render(request, 'users/earnings_statement.html', {'business': business})

    orders = Order.objects.filter(
        business=business,
        status='paid',
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).order_by('created_at')

    total_gross      = sum(o.total_amount for o in orders)
    total_commission = sum((o.commission_fee or Decimal('0.00')) for o in orders)
    total_net        = total_gross - total_commission

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()
    title_style   = ParagraphStyle('Title',    parent=styles['Heading1'], fontSize=18, spaceAfter=4)
    subtitle_style= ParagraphStyle('Subtitle', parent=styles['Normal'],   fontSize=10, textColor=colors.grey, spaceAfter=16)
    section_style = ParagraphStyle('Section',  parent=styles['Heading2'], fontSize=12, spaceBefore=12, spaceAfter=6)
    footer_style  = ParagraphStyle('Footer',   parent=styles['Normal'],   fontSize=7,  textColor=colors.grey)

    story = []
    story.append(Paragraph('PayFusion', styles['Heading1']))
    story.append(Paragraph('Earnings Statement', title_style))
    story.append(Paragraph(
        f'{business.name} | {date_from.strftime("%d %b %Y")} to {date_to.strftime("%d %b %Y")}',
        subtitle_style
    ))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph('Summary', section_style))

    summary_data = [
        ['Total Orders',     str(orders.count())],
        ['Gross Revenue',    f'N{total_gross:,.2f}'],
        ['Total Commission', f'N{total_commission:,.2f}'],
        ['Net Earnings',     f'N{total_net:,.2f}'],
    ]
    summary_table = Table(summary_data, colWidths=[80*mm, 60*mm])
    summary_table.setStyle(TableStyle([
        ('FONTNAME',       (0,0),(-1,-1), 'Helvetica'),
        ('FONTSIZE',       (0,0),(-1,-1), 10),
        ('FONTNAME',       (0,3),(-1,3),  'Helvetica-Bold'),
        ('TEXTCOLOR',      (0,3),(-1,3),  colors.HexColor('#10b981')),
        ('ROWBACKGROUNDS', (0,0),(-1,-1), [colors.whitesmoke, colors.white]),
        ('BOX',            (0,0),(-1,-1), 0.5, colors.lightgrey),
        ('INNERGRID',      (0,0),(-1,-1), 0.25, colors.lightgrey),
        ('TOPPADDING',     (0,0),(-1,-1), 4),
        ('BOTTOMPADDING',  (0,0),(-1,-1), 4),
        ('LEFTPADDING',    (0,0),(-1,-1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('Order Breakdown', section_style))

    if orders:
        order_data = [['Date','Reference','Gross (N)','Commission (N)','Net (N)']]
        for order in orders:
            commission = order.commission_fee or Decimal('0.00')
            net = order.total_amount - commission
            order_data.append([
                order.created_at.strftime('%d %b %Y'),
                order.reference,
                f'{order.total_amount:,.2f}',
                f'{commission:,.2f}',
                f'{net:,.2f}',
            ])
        order_data.append(['TOTAL','',f'{total_gross:,.2f}',f'{total_commission:,.2f}',f'{total_net:,.2f}'])

        orders_table = Table(order_data, colWidths=[28*mm,45*mm,32*mm,36*mm,30*mm])
        orders_table.setStyle(TableStyle([
            ('BACKGROUND',     (0,0), (-1,0),  colors.HexColor('#1e293b')),
            ('TEXTCOLOR',      (0,0), (-1,0),  colors.white),
            ('FONTNAME',       (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',       (0,0), (-1,0),  9),
            ('FONTNAME',       (0,1), (-1,-2), 'Helvetica'),
            ('FONTSIZE',       (0,1), (-1,-2), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.whitesmoke]),
            ('FONTNAME',       (0,-1),(-1,-1), 'Helvetica-Bold'),
            ('BACKGROUND',     (0,-1),(-1,-1), colors.HexColor('#f0fdf4')),
            ('TEXTCOLOR',      (0,-1),(-1,-1), colors.HexColor('#10b981')),
            ('ALIGN',          (2,0), (-1,-1), 'RIGHT'),
            ('ALIGN',          (0,0), (1,-1),  'LEFT'),
            ('BOX',            (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('INNERGRID',      (0,0), (-1,-1), 0.25, colors.lightgrey),
            ('TOPPADDING',     (0,0), (-1,-1), 4),
            ('BOTTOMPADDING',  (0,0), (-1,-1), 4),
            ('LEFTPADDING',    (0,0), (-1,-1), 6),
            ('RIGHTPADDING',   (0,0), (-1,-1), 6),
        ]))
        story.append(orders_table)
    else:
        story.append(Paragraph('No paid orders found in this date range.', styles['Normal']))

    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f'Generated by PayFusion on {timezone.now().strftime("%d %b %Y at %H:%M")}. '
        f'Commission figures are taken from recorded order data.',
        footer_style
    ))

    doc.build(story)
    buffer.seek(0)
    filename = (
        f'PayFusion_Earnings_{business.slug}_'
        f'{date_from.strftime("%Y%m%d")}_to_{date_to.strftime("%Y%m%d")}.pdf'
    )
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response