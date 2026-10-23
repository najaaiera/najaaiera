import 'dart:io';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:table_calendar/table_calendar.dart';

import '../models.dart';
import '../store.dart';
import 'expense_form_screen.dart';
import 'receipt_viewer.dart';
import 'summary_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.store});

  final ExpenseStore store;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  DateTime _focusedDay = dayOnly(DateTime.now());
  DateTime _selectedDay = dayOnly(DateTime.now());
  CalendarFormat _format = CalendarFormat.month;

  ExpenseStore get store => widget.store;

  Future<void> _openForm({Expense? expense}) async {
    await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => ExpenseFormScreen(
        store: store,
        expense: expense,
        initialDate: _selectedDay,
      ),
    ));
  }

  Future<void> _editDayNote() async {
    final ctrl = TextEditingController(text: store.dayNote(_selectedDay));
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Note for ${DateFormat('d MMM yyyy').format(_selectedDay)}'),
        content: SizedBox(
          width: 400,
          child: TextField(
            controller: ctrl,
            autofocus: true,
            maxLines: 6,
            minLines: 3,
            decoration: const InputDecoration(
              hintText: 'Write anything about this day…',
              border: OutlineInputBorder(),
            ),
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text),
              child: const Text('Save')),
        ],
      ),
    );
    if (result != null) await store.setDayNote(_selectedDay, result);
  }

  Future<void> _editCurrency() async {
    final ctrl = TextEditingController(text: store.currency);
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Currency symbol'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'e.g. RM, \$, €'),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text),
              child: const Text('Save')),
        ],
      ),
    );
    if (result != null) await store.setCurrency(result);
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          appBar: AppBar(
            title: const Text('Daily Expense'),
            actions: [
              IconButton(
                tooltip: 'Go to today',
                icon: const Icon(Icons.today),
                onPressed: () => setState(() {
                  _selectedDay = _focusedDay = dayOnly(DateTime.now());
                }),
              ),
              IconButton(
                tooltip: 'Monthly summary',
                icon: const Icon(Icons.pie_chart_outline),
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) =>
                      SummaryScreen(store: store, month: _focusedDay),
                )),
              ),
              IconButton(
                tooltip: 'Currency',
                icon: const Icon(Icons.settings_outlined),
                onPressed: _editCurrency,
              ),
            ],
          ),
          floatingActionButton: FloatingActionButton.extended(
            onPressed: () => _openForm(),
            icon: const Icon(Icons.add),
            label: const Text('Add expense'),
          ),
          body: LayoutBuilder(builder: (context, c) {
            // Side-by-side on wide screens (Windows / tablets).
            if (c.maxWidth >= 900) {
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 460,
                    child: ListView(children: [
                      _monthCard(),
                      _calendar(),
                    ]),
                  ),
                  const VerticalDivider(width: 1),
                  Expanded(
                    child: ListView(
                      padding: const EdgeInsets.only(bottom: 96),
                      children: _dayDetails(),
                    ),
                  ),
                ],
              );
            }
            return ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [_monthCard(), _calendar(), ..._dayDetails()],
            );
          }),
        );
      },
    );
  }

  Widget _monthCard() {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      color: scheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Total for ${DateFormat('MMMM yyyy').format(_focusedDay)}',
                      style: TextStyle(color: scheme.onPrimaryContainer)),
                  const SizedBox(height: 4),
                  Text(
                    store.money(store.totalForMonth(_focusedDay)),
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: scheme.onPrimaryContainer),
                  ),
                ],
              ),
            ),
            Icon(Icons.account_balance_wallet_outlined,
                size: 40, color: scheme.onPrimaryContainer),
          ],
        ),
      ),
    );
  }

  Widget _calendar() {
    final scheme = Theme.of(context).colorScheme;
    return TableCalendar<Expense>(
      firstDay: DateTime(2000),
      lastDay: DateTime(2100, 12, 31),
      focusedDay: _focusedDay,
      calendarFormat: _format,
      startingDayOfWeek: StartingDayOfWeek.monday,
      selectedDayPredicate: (d) => isSameDay(d, _selectedDay),
      eventLoader: store.forDay,
      availableCalendarFormats: const {
        CalendarFormat.month: 'Month',
        CalendarFormat.twoWeeks: '2 weeks',
        CalendarFormat.week: 'Week',
      },
      onFormatChanged: (f) => setState(() => _format = f),
      onDaySelected: (selected, focused) => setState(() {
        _selectedDay = dayOnly(selected);
        _focusedDay = dayOnly(focused);
      }),
      onPageChanged: (focused) => setState(() => _focusedDay = focused),
      calendarStyle: CalendarStyle(
        todayDecoration: BoxDecoration(
            color: scheme.secondaryContainer, shape: BoxShape.circle),
        todayTextStyle: TextStyle(color: scheme.onSecondaryContainer),
        selectedDecoration:
            BoxDecoration(color: scheme.primary, shape: BoxShape.circle),
        markerDecoration:
            BoxDecoration(color: scheme.tertiary, shape: BoxShape.circle),
        markersMaxCount: 3,
      ),
      calendarBuilders: CalendarBuilders(
        // Small icon on days that have a day note.
        markerBuilder: (context, day, events) {
          final hasNote = store.hasDayNote(day);
          if (events.isEmpty && !hasNote) return null;
          return Positioned(
            bottom: 2,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (var i = 0; i < events.length.clamp(0, 3); i++)
                  Container(
                    width: 5,
                    height: 5,
                    margin: const EdgeInsets.symmetric(horizontal: 0.5),
                    decoration: BoxDecoration(
                        color: scheme.tertiary, shape: BoxShape.circle),
                  ),
                if (hasNote)
                  Icon(Icons.sticky_note_2, size: 9, color: scheme.secondary),
              ],
            ),
          );
        },
      ),
    );
  }

  List<Widget> _dayDetails() {
    final items = store.forDay(_selectedDay);
    final note = store.dayNote(_selectedDay);
    final theme = Theme.of(context);

    return [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
        child: Row(
          children: [
            Expanded(
              child: Text(
                DateFormat('EEEE, d MMM yyyy').format(_selectedDay),
                style: theme.textTheme.titleMedium,
              ),
            ),
            Text(
              store.money(store.totalForDay(_selectedDay)),
              style: theme.textTheme.titleMedium
                  ?.copyWith(fontWeight: FontWeight.bold),
            ),
          ],
        ),
      ),
      Card(
        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: ListTile(
          leading: const Icon(Icons.sticky_note_2_outlined),
          title: Text(note.isEmpty ? 'Add a note for this day' : note,
              style: note.isEmpty
                  ? TextStyle(color: theme.colorScheme.outline)
                  : null),
          trailing: const Icon(Icons.edit_outlined),
          onTap: _editDayNote,
        ),
      ),
      if (items.isEmpty)
        Padding(
          padding: const EdgeInsets.all(32),
          child: Center(
            child: Text('No expenses on this day.',
                style: TextStyle(color: theme.colorScheme.outline)),
          ),
        ),
      for (final e in items) _expenseTile(e),
    ];
  }

  Widget _expenseTile(Expense e) {
    final hasReceipt = e.receiptPath != null && File(e.receiptPath!).existsSync();
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: ListTile(
        leading: hasReceipt
            ? GestureDetector(
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => ReceiptViewer(path: e.receiptPath!))),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: Image.file(File(e.receiptPath!),
                      width: 48, height: 48, fit: BoxFit.cover,
                      cacheWidth: 144),
                ),
              )
            : CircleAvatar(child: Icon(categoryIcon(e.category))),
        title: Text(e.title.isEmpty ? e.category : e.title),
        subtitle: Text(
          [
            e.category,
            DateFormat('h:mm a').format(e.date),
            if (e.note.isNotEmpty) e.note,
          ].join(' • '),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: Text(store.money(e.amount),
            style: const TextStyle(fontWeight: FontWeight.bold)),
        onTap: () => _openForm(expense: e),
      ),
    );
  }
}

IconData categoryIcon(String c) => switch (c) {
      'Food' => Icons.restaurant,
      'Transport' => Icons.directions_car,
      'Shopping' => Icons.shopping_bag,
      'Bills' => Icons.receipt_long,
      'Health' => Icons.local_hospital,
      'Entertainment' => Icons.movie,
      _ => Icons.category,
    };
