# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-06-26 13:07
from __future__ import unicode_literals

from django.conf import settings
import django.contrib.postgres.fields.jsonb
from django.db import migrations, models
import django.db.models.deletion
import django_intenum
import hsreplaynet.webhooks.models
import hsreplaynet.webhooks.validators
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('webhooks', '0002_webhook_secret'),
    ]

    operations = [
        migrations.CreateModel(
            name='Event',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('type', models.CharField(db_index=True, max_length=50)),
                ('data', django.contrib.postgres.fields.jsonb.JSONField(blank=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='WebhookDelivery',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('url', models.URLField()),
                ('request_headers', django.contrib.postgres.fields.jsonb.JSONField()),
                ('request_body', models.TextField(blank=True)),
                ('response_status', models.PositiveSmallIntegerField(null=True)),
                ('response_headers', django.contrib.postgres.fields.jsonb.JSONField(blank=True)),
                ('response_body', models.TextField(blank=True)),
                ('completed_time', models.PositiveIntegerField()),
                ('success', models.BooleanField()),
                ('error', models.TextField(blank=True)),
                ('traceback', models.TextField(blank=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='WebhookEndpoint',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('url', models.URLField(help_text='The URL the webhook will POST to.', validators=[hsreplaynet.webhooks.validators.WebhookURLValidator()])),
                ('secret', models.UUIDField(default=uuid.uuid4, editable=False, help_text='Salt for the X-Webhook-Signature header sent with the payload')),
                ('timeout', models.PositiveSmallIntegerField(default=10, help_text='Timeout (in seconds) the triggers have before they fail.')),
                ('is_active', models.BooleanField(default=True, help_text='Whether the listener is enabled.')),
                ('is_deleted', models.BooleanField(default=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webhook_endpoints', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RemoveField(
            model_name='webhooktrigger',
            name='webhook',
        ),
        migrations.RenameField(
            model_name='webhook',
            old_name='modified',
            new_name='updated',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='is_active',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='is_deleted',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='max_triggers',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='secret',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='timeout',
        ),
        migrations.RemoveField(
            model_name='webhook',
            name='user',
        ),
        migrations.AddField(
            model_name='webhook',
            name='payload',
            field=django.contrib.postgres.fields.jsonb.JSONField(default={}),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='webhook',
            name='status',
            field=django_intenum.IntEnumField(choices=[(0, 'UNKNOWN'), (1, 'PENDING'), (2, 'IN_PROGRESS'), (3, 'SUCCESS'), (4, 'ERROR')], default=0, validators=[django_intenum.IntEnumValidator(hsreplaynet.webhooks.models.WebhookStatus)]),
        ),
        migrations.AlterField(
            model_name='webhook',
            name='url',
            field=models.URLField(),
        ),
        migrations.DeleteModel(
            name='WebhookTrigger',
        ),
        migrations.AddField(
            model_name='webhookdelivery',
            name='webhook',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deliveries', to='webhooks.Webhook'),
        ),
        migrations.AddField(
            model_name='webhook',
            name='endpoint',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='webhooks', to='webhooks.WebhookEndpoint'),
        ),
        migrations.AddField(
            model_name='webhook',
            name='event',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='webhooks', to='webhooks.Event'),
        ),
    ]
