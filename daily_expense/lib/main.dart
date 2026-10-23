import 'package:flutter/material.dart';

import 'screens/home_screen.dart';
import 'store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final store = ExpenseStore();
  await store.load();
  runApp(DailyExpenseApp(store: store));
}

class DailyExpenseApp extends StatelessWidget {
  const DailyExpenseApp({super.key, required this.store});

  final ExpenseStore store;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Daily Expense',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.teal,
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: Colors.teal,
        brightness: Brightness.dark,
        useMaterial3: true,
      ),
      home: HomeScreen(store: store),
    );
  }
}
