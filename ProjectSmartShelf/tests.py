from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from checkout.models import Product


class LCDManagementViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff',
            password='password123',
            is_staff=True,
        )
        self.client.login(username='staff', password='password123')
        self.product = Product.objects.create(
            name='Milk',
            category='Dairy',
            price=Decimal('12.50'),
            inventory_level=20,
            lcd_id=2,
        )

    def test_lcd_management_page_renders_products_and_available_lcds(self):
        response = self.client.get(reverse('lcd_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LCD Management')
        self.assertContains(response, 'Milk')
        self.assertContains(response, 'LCD 1')
        self.assertContains(response, 'LCD 2')
        self.assertContains(response, 'LCD 3')
        self.assertContains(response, 'Refresh')
        self.assertNotContains(response, 'Display Routing')

    def test_update_assignment_sets_lcd_id(self):
        response = self.client.post(
            reverse('lcd_management'),
            {
                'action': 'update_assignment',
                'product_id': self.product.id,
                'lcd_id': '3',
            },
        )

        self.assertRedirects(response, reverse('lcd_management'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.lcd_id, 3)

    def test_update_assignment_rejects_lcd_already_assigned_to_another_product(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )

        response = self.client.post(
            reverse('lcd_management'),
            {
                'action': 'update_assignment',
                'product_id': self.product.id,
                'lcd_id': '1',
            },
        )

        self.assertRedirects(response, reverse('lcd_management'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.lcd_id, 2)

    def test_assignment_options_exclude_lcd_assigned_to_another_product(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )

        response = self.client.get(reverse('lcd_management'))
        milk = next(
            product for product in response.context['products']
            if product.id == self.product.id
        )

        self.assertEqual(milk.assignment_lcd_ids, [2, 3])

    def test_assigned_products_are_listed_before_unassigned_products(self):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )
        Product.objects.create(
            name='Apples',
            category='Produce',
            price=Decimal('5.25'),
            inventory_level=18,
            lcd_id=0,
        )

        response = self.client.get(reverse('lcd_management'))
        product_names = [product.name for product in response.context['products']]

        self.assertEqual(product_names, ['Bread', 'Milk', 'Apples'])

    def test_unassigned_products_render_without_lcd_zero(self):
        self.product.lcd_id = 0
        self.product.save(update_fields=['lcd_id'])

        response = self.client.get(reverse('lcd_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not assigned')
        self.assertNotContains(response, 'LCD 0')

    @patch('ProjectSmartShelf.views.send_price_update', return_value=True)
    def test_sync_all_publishes_current_product_prices_and_skips_unassigned(self, mock_send_price_update):
        Product.objects.create(
            name='Bread',
            category='Bakery',
            price=Decimal('8.75'),
            inventory_level=12,
            lcd_id=1,
        )
        Product.objects.create(
            name='Apples',
            category='Produce',
            price=Decimal('5.25'),
            inventory_level=18,
            lcd_id=0,
        )

        response = self.client.post(reverse('lcd_management'), {'action': 'sync_all'})

        self.assertRedirects(response, reverse('lcd_management'))
        self.assertEqual(mock_send_price_update.call_count, 2)
        mock_send_price_update.assert_any_call(2, 'Milk', '12.50')
        mock_send_price_update.assert_any_call(1, 'Bread', '8.75')
