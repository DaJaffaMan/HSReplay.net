# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2017-07-25 03:22
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('decks', '0002_sql_triggers'),
    ]

    operations = [
        migrations.CreateModel(
            name='ArchetypeTrainingDeck',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_validation_deck', models.BooleanField()),
                ('deck', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='decks.Deck')),
            ],
        ),
    ]
