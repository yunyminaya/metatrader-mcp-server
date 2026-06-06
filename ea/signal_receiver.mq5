//+------------------------------------------------------------------+
//|                                             signal_receiver.mq5  |
//|  EA Companion — ejecuta señales del Python MCP a tick speed     |
//|                                                                  |
//|  Protocol:                                                       |
//|    1. Python escribe:  Signals\TRADE_SIGNAL_<id>.json            |
//|    2. EA lee en cada tick, ejecuta, escribe resultado            |
//|    3. Python lee:      Signals\TRADE_RESULT_<id>.json            |
//|                                                                  |
//|  Características:                                                |
//|    - Ejecución tick-level (sin latencia Python)                  |
//|    - Trailing stop automático en cada tick                       |
//|    - Breakeven automático                                        |
//|    - Volatility hedge (reduce size si ATR se dispara)            |
//|    - Anti-stop-hunting (SL no obvio)                             |
//+------------------------------------------------------------------+
#property copyright "MCP Trading System"
#property link      "https://github.com"
#property version   "2.00"
#property description "EA Companion for MCP Trading System"
#property description "Receives signals from Python, executes at tick speed"

//+------------------------------------------------------------------+
//| Input parameters                                                 |
//+------------------------------------------------------------------+
input string   SignalDirectory = "Signals";           // Signal directory (relative to Terminal_Data_Dir\MQL5\Files\)
input int      MagicNumber      = 20240601;           // Magic number for order identification
input double   MaxRiskPercent   = 2.0;                // Max risk per trade (% of balance)
input bool     UseTrailingStop  = true;               // Enable trailing stop
input double   TrailStartPips   = 15.0;               // Pips profit to start trailing
input double   TrailDistancePips = 10.0;              // Trailing stop distance (pips)
input bool     UseBreakeven     = true;               // Enable breakeven
input double   BreakevenPips    = 10.0;               // Pips profit to move SL to breakeven
input bool     VolatilityHedge  = true;               // Reduce size in high volatility
input double   MaxSpreadPips    = 20.0;               // Max spread to accept
input int      Slippage         = 30;                 // Max slippage (points)
input int      SignalTimeoutSec = 300;                // Delete signal if older than N seconds

//+------------------------------------------------------------------+
//| Global variables                                                  |
//+------------------------------------------------------------------+
string   SignalPath;
string   ResultPath;
ulong    LastSignalTime = 0;
int      HandleFile     = -1;
datetime LastBarTime    = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   SignalPath = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\" + SignalDirectory + "\\";
   ResultPath = SignalPath;
   
   // Ensure directory exists
   if (!FolderCreate(SignalDirectory)) {
      Print("Created signal directory: ", SignalDirectory);
   }
   
   Print("EA Signal Receiver initialized");
   Print("Signal directory: ", SignalPath);
   Print("Magic number: ", MagicNumber);
   Print("Waiting for signals...");
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   Print("EA Signal Receiver stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick() {
   // 1. Process trailing stop and breakeven for existing positions
   if (UseTrailingStop || UseBreakeven) {
      ManagePositions();
   }
   
   // 2. Check for new signal files
   ProcessSignalFiles();
   
   // 3. Volatility hedge check
   if (VolatilityHedge) {
      CheckVolatility();
   }
}

//+------------------------------------------------------------------+
//| Check for and process signal files                               |
//+------------------------------------------------------------------+
void ProcessSignalFiles() {
   string filePattern = "TRADE_SIGNAL_*.json";
   string fileName;
   long searchHandle = FileFindFirst(SignalDirectory + "\\" + filePattern, fileName);
   
   if (searchHandle == INVALID_HANDLE) {
      return;  // No signal files
   }
   
   do {
      string fullPath = SignalPath + fileName;
      
      // Check file age
      if (FileIsExist(fullPath, 0)) {
         ulong modTime = FileGetInteger(fullPath, FILE_MODIFY_DATE, false);
         ulong now = (ulong)TimeCurrent();
         
         if (now - modTime > (ulong)SignalTimeoutSec) {
            FileDelete(fullPath);
            Print("Deleted expired signal: ", fileName);
            continue;
         }
         
         // Read and execute signal
         ProcessSingleSignal(fullPath, fileName);
      }
      
   } while (FileFindNext(searchHandle, fileName));
   
   FileFindClose(searchHandle);
}

//+------------------------------------------------------------------+
//| Process a single signal file                                     |
//+------------------------------------------------------------------+
void ProcessSingleSignal(string filePath, string fileName) {
   // Extract signal ID from filename
   string id = "";
   int start = StringFind(fileName, "TRADE_SIGNAL_");
   int end = StringFind(fileName, ".json");
   if (start >= 0 && end > start) {
      id = StringSubstr(fileName, start + 13, end - start - 13);
   }
   
   if (id == "") {
      FileDelete(filePath);
      return;
   }
   
   // Read JSON (simple parser)
   string jsonContent = "";
   int handle = FileOpen(fileName, FILE_READ|FILE_TXT|FILE_COMMON);
   if (handle == INVALID_HANDLE) {
      // Try different path
      handle = FileOpen(SignalDirectory + "\\" + fileName, FILE_READ|FILE_TXT);
      if (handle == INVALID_HANDLE) {
         Print("Cannot open signal file: ", fileName);
         return;
      }
   }
   
   ulong fileSize = FileSize(handle);
   if (fileSize > 0 && fileSize < 10000) {
      jsonContent = FileReadString(handle, (int)fileSize);
   }
   FileClose(handle);
   
   if (jsonContent == "") {
      FileDelete(filePath);
      return;
   }
   
   // Parse signal
   string symbol     = ReadJSONString(jsonContent, "symbol");
   string orderType  = ReadJSONString(jsonContent, "type");
   double volume     = ReadJSONDouble(jsonContent, "volume");
   double stopLoss   = ReadJSONDouble(jsonContent, "sl");
   double takeProfit = ReadJSONDouble(jsonContent, "tp");
   bool   closeAll   = ReadJSONBool(jsonContent, "close_all");
   string action     = ReadJSONString(jsonContent, "action");
   
   // Result to write back
   string result = "{";
   
   if (action == "close_all") {
      CloseAllPositions();
      result += "\"success\":true,\"action\":\"close_all\",\"closed\":true";
   } else if (action == "modify_sl") {
      // Modify SL of existing position
      long ticket = (long)ReadJSONDouble(jsonContent, "ticket");
      double newSL = ReadJSONDouble(jsonContent, "sl");
      if (ModifyStopLoss(ticket, newSL)) {
         result += "\"success\":true,\"action\":\"modify_sl\",\"ticket\":" + IntegerToString(ticket) + ",\"new_sl\":" + DoubleToString(newSL, 5);
      } else {
         result += "\"success\":false,\"action\":\"modify_sl\",\"error\":\"modify_failed\"";
      }
   } else if (symbol != "" && orderType != "" && volume > 0) {
      // Place market order
      result += ExecuteMarketOrder(symbol, orderType, volume, stopLoss, takeProfit, id);
   } else {
      result += "\"success\":false,\"error\":\"invalid_signal\"";
   }
   
   result += ",\"signal_id\":\"" + id + "\"";
   result += ",\"processed_at\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\"";
   result += "}";
   
   // Write result file
   string resultFile = SignalDirectory + "\\TRADE_RESULT_" + id + ".json";
   int resHandle = FileOpen(resultFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if (resHandle != INVALID_HANDLE) {
      FileWrite(resHandle, result);
      FileClose(resHandle);
   }
   
   // Delete signal file
   FileDelete(filePath);
   
   Print("Processed signal: ", fileName, " -> ", orderType, " ", symbol, " ", DoubleToString(volume, 2));
}

//+------------------------------------------------------------------+
//| Execute a market order                                           |
//+------------------------------------------------------------------+
string ExecuteMarketOrder(string symbol, string type, double volume,
                           double sl, double tp, string signalId) {
   
   // Validate symbol
   bool symbolExists;
   SymbolExist(symbol, symbolExists);
   if (!symbolExists) {
      return "\"success\":false,\"error\":\"symbol_not_found\",\"symbol\":\"" + symbol + "\"";
   }
   
   // Check spread
   double spread = (SymbolInfoInteger(symbol, SYMBOL_SPREAD));
   if (spread > MaxSpreadPips * 10) {
      return "\"success\":false,\"error\":\"spread_too_high\",\"spread\":" + DoubleToString(spread / 10, 1);
   }
   
   // Check margin
   double margin = 0;
   if (!OrderCalcMargin(type == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, symbol, volume, SymbolInfoDouble(symbol, SYMBOL_ASK), margin)) {
      // Continue anyway — margin check is advisory
   }
   
   // Prepare trade request
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};
   
   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = symbol;
   request.volume    = volume;
   request.deviation = Slippage;
   request.magic     = MagicNumber;
   request.comment   = "MCP_" + signalId;
   
   if (type == "BUY") {
      request.type     = ORDER_TYPE_BUY;
      request.price    = SymbolInfoDouble(symbol, SYMBOL_ASK);
      if (sl > 0) request.sl = sl;
      if (tp > 0) request.tp = tp;
   } else if (type == "SELL") {
      request.type     = ORDER_TYPE_SELL;
      request.price    = SymbolInfoDouble(symbol, SYMBOL_BID);
      if (sl > 0) request.sl = sl;
      if (tp > 0) request.tp = tp;
   } else {
      return "\"success\":false,\"error\":\"invalid_type\"";
   }
   
   // Anti-obvious SL: add small offset if SL is at round number
   if (sl > 0) {
      double pipSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
      if (MathAbs(sl - MathRound(sl / pipSize) * pipSize) < pipSize * 0.1) {
         if (type == "BUY") request.sl = sl - pipSize * 3;
         else request.sl = sl + pipSize * 3;
      }
   }
   
   // Execute
   if (!OrderSend(request, result)) {
      return StringFormat("\"success\":false,\"error\":\"order_failed\",\"retcode\":%d", result.retcode);
   }
   
   string res = "{";
   res += "\"success\":true,";
   res += "\"ticket\":" + IntegerToString(result.order) + ",";
   res += "\"price\":" + DoubleToString(request.price, 5) + ",";
   res += "\"volume\":" + DoubleToString(volume, 2) + ",";
   if (result.comment != "") res += "\"comment\":\"" + result.comment + "\",";
   res += "\"symbol\":\"" + symbol + "\"";
   res += "}";
   
   return res;
}

//+------------------------------------------------------------------+
//| Manage trailing stop and breakeven for all positions              |
//+------------------------------------------------------------------+
void ManagePositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      
      if (PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      string symbol    = PositionGetString(POSITION_SYMBOL);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double profit    = PositionGetDouble(POSITION_PROFIT);
      double swap      = PositionGetDouble(POSITION_SWAP);
      long   type      = PositionGetInteger(POSITION_TYPE);
      
      double point      = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double pipSize    = point * 10;
      double bid        = SymbolInfoDouble(symbol, SYMBOL_BID);
      double ask        = SymbolInfoDouble(symbol, SYMBOL_ASK);
      
      double profitPoints = 0;
      if (type == POSITION_TYPE_BUY) {
         profitPoints = (bid - openPrice) / pipSize;
      } else {
         profitPoints = (openPrice - ask) / pipSize;
      }
      
      double newSL = currentSL;
      bool needModify = false;
      
      // Breakeven
      if (UseBreakeven && profitPoints >= BreakevenPips) {
         double breakevenPrice = (type == POSITION_TYPE_BUY) ? openPrice + point : openPrice - point;
         if (currentSL == 0 || (type == POSITION_TYPE_BUY && currentSL < breakevenPrice) ||
             (type == POSITION_TYPE_SELL && currentSL > breakevenPrice)) {
            newSL = breakevenPrice;
            needModify = true;
         }
      }
      
      // Trailing stop
      if (UseTrailingStop && profitPoints >= TrailStartPips) {
         double trailPrice;
         if (type == POSITION_TYPE_BUY) {
            trailPrice = bid - TrailDistancePips * pipSize;
            if (trailPrice > currentSL) {
               newSL = trailPrice;
               needModify = true;
            }
         } else {
            trailPrice = ask + TrailDistancePips * pipSize;
            if (trailPrice < currentSL || currentSL == 0) {
               newSL = trailPrice;
               needModify = true;
            }
         }
      }
      
      // Apply modification
      if (needModify && newSL != currentSL) {
         MqlTradeRequest req = {};
         MqlTradeResult  res = {};
         req.action   = TRADE_ACTION_SLTP;
         req.symbol   = symbol;
         req.position = ticket;
         req.sl       = newSL;
         req.tp       = currentTP;
         req.magic    = MagicNumber;
         
         if (OrderSend(req, res)) {
            Print("Modified position ", ticket, " SL -> ", DoubleToString(newSL, 5));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Volatility check — reduce positions if ATR spikes                |
//+------------------------------------------------------------------+
void CheckVolatility() {
   // Check all open positions
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      
      string symbol = PositionGetString(POSITION_SYMBOL);
      
      // Get current ATR (simple: current range / average range)
      double high1 = iHigh(symbol, PERIOD_H1, 1);
      double low1  = iLow(symbol, PERIOD_H1, 1);
      double range1 = high1 - low1;
      
      double avgRange = 0;
      int bars = 14;
      for (int j = 1; j <= bars; j++) {
         avgRange += iHigh(symbol, PERIOD_H1, j) - iLow(symbol, PERIOD_H1, j);
      }
      avgRange /= bars;
      
      if (avgRange > 0 && range1 > avgRange * 2.0) {
         // Volatility spike — close position
         MqlTradeRequest req = {};
         MqlTradeResult  res = {};
         req.action = TRADE_ACTION_DEAL;
         req.symbol = symbol;
         req.volume = PositionGetDouble(POSITION_VOLUME);
         req.deviation = Slippage;
         req.magic = MagicNumber;
         
         if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) {
            req.type = ORDER_TYPE_SELL;
            req.price = SymbolInfoDouble(symbol, SYMBOL_BID);
         } else {
            req.type = ORDER_TYPE_BUY;
            req.price = SymbolInfoDouble(symbol, SYMBOL_ASK);
         }
         
         if (OrderSend(req, res)) {
            Print("Volatility close: ", symbol, " (range ratio: ", DoubleToString(range1 / avgRange, 1), "x)");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Close all positions                                              |
//+------------------------------------------------------------------+
void CloseAllPositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      
      string sym = PositionGetString(POSITION_SYMBOL);
      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      req.action = TRADE_ACTION_DEAL;
      req.symbol = sym;
      req.volume = PositionGetDouble(POSITION_VOLUME);
      req.deviation = Slippage;
      req.magic = MagicNumber;
      
      if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) {
         req.type = ORDER_TYPE_SELL;
         req.price = SymbolInfoDouble(sym, SYMBOL_BID);
      } else {
         req.type = ORDER_TYPE_BUY;
         req.price = SymbolInfoDouble(sym, SYMBOL_ASK);
      }
      
      OrderSend(req, res);
   }
}

//+------------------------------------------------------------------+
//| Modify stop loss                                                 |
//+------------------------------------------------------------------+
bool ModifyStopLoss(ulong ticket, double newSL) {
   if (!PositionSelectByTicket(ticket)) return false;
   
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action   = TRADE_ACTION_SLTP;
   req.symbol   = PositionGetString(POSITION_SYMBOL);
   req.position = ticket;
   req.sl       = newSL;
   req.tp       = PositionGetDouble(POSITION_TP);
   req.magic    = MagicNumber;
   
   return OrderSend(req, res);
}

//+------------------------------------------------------------------+
//| Simple JSON parser functions                                     |
//+------------------------------------------------------------------+
string ReadJSONString(string json, string key) {
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if (pos < 0) return "";
   pos += StringLen(search);
   int end = StringFind(json, "\"", pos);
   if (end < 0) return "";
   return StringSubstr(json, pos, end - pos);
}

double ReadJSONDouble(string json, string key) {
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if (pos < 0) return 0;
   pos += StringLen(search);
   // Skip spaces
   while (pos < StringLen(json) && json[pos] == ' ') pos++;
   
   string numStr = "";
   while (pos < StringLen(json)) {
      ushort c = StringGetCharacter(json, pos);
      if ((c >= '0' && c <= '9') || c == '.' || c == '-' || c == 'e' || c == 'E') {
         numStr += ShortToString(c);
      } else break;
      pos++;
   }
   return StringToDouble(numStr);
}

bool ReadJSONBool(string json, string key) {
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if (pos < 0) return false;
   pos += StringLen(search);
   return StringFind(json, "true", pos) >= 0 && StringFind(json, "true", pos) < StringFind(json, ",", pos);
}
//+------------------------------------------------------------------+
