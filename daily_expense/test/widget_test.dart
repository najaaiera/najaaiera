import 'dart:io';

import 'package:daily_expense/main.dart';
import 'package:daily_expense/models.dart';
import 'package:daily_expense/store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Directory tmp;

  setUp(() => tmp = Directory.systemTemp.createTempSync('daily_expense_test'));
  tearDown(() => tmp.deleteSync(recursive: true));

  test('store saves, totals and reloads expenses and notes', () async {
    final store = ExpenseStore(baseDir: tmp);
    await store.load();
    final day = DateTime(2026, 10, 23, 12);
    await store.upsert(Expense(
        id: 'a', title: 'Lunch', amount: 12.5, category: 'Food', date: day));
    await store.upsert(Expense(
        id: 'b', title: 'Grab', amount: 7.5, category: 'Transport', date: day));
    await store.upsert(Expense(
        id: 'c',
        title: 'Rent',
        amount: 800,
        category: 'Bills',
        date: DateTime(2026, 10, 1)));
    await store.setDayNote(day, 'Payday');

    final reloaded = ExpenseStore(baseDir: tmp);
    await reloaded.load();
    expect(reloaded.totalForDay(day), 20);
    expect(reloaded.totalForMonth(day), 820);
    expect(reloaded.categoryTotals(day)['Food'], 12.5);
    expect(reloaded.dayNote(day), 'Payday');

    await reloaded.delete('a');
    expect(reloaded.totalForDay(day), 7.5);
  });

  testWidgets('home screen shows calendar and add button', (tester) async {
    final store = ExpenseStore(baseDir: tmp);
    await tester.runAsync(store.load);
    await tester.pumpWidget(DailyExpenseApp(store: store));
    expect(find.text('Daily Expense'), findsOneWidget);
    expect(find.text('Add expense'), findsOneWidget);
    expect(find.text('Add a note for this day'), findsOneWidget);
  });
}
