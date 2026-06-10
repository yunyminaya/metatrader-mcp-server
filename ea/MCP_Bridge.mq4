//+------------------------------------------------------------------+
//|                                                  MCP_Bridge.mq4  |
//|                        MetaTrader MCP Server - MT4 Bridge EA     |
//|                    Bridge entre Python y MT4 via archivos JSON    |
//+------------------------------------------------------------------+
//| Este EA monitorea una carpeta por comandos JSON enviados por     |
//| Python, los ejecuta en MT4 y escribe las respuestas como JSON.   |
//|                                                                   |
//| INSTALACIÓN:                                                      |
//| 1. Copiar este archivo a: MQL4/Experts/                          |
//| 2. Compilar en MetaEditor                                         |
//| 3. Arrastrar a cualquier gráfico en MT4                          |
//| 4. En Tools > Options > Expert Advisors:                         |
//|    - Marcar "Allow live trading"                                  |
//|    - Marcar "Allow DLL imports"                                   |
//| 5. Configurar la ruta del bridge en los inputs                   |
//+------------------------------------------------------------------+
#property copyright "MetaTrader MCP Server"
#property link      "https://github.com/yunyminaya/metatrader-mcp-server"
#property version   "1.00"
#property strict

//--- Inputs
input string BridgePath = "";           // Ruta del bridge (vacío = auto-detect)
input int    PollInterval = 500;        // Intervalo de polling en ms
input int    CommandTimeout = 30;       // Timeout de comandos en segundos
input int    MagicNumber = 123456;      // Magic number para órdenes

//--- Variables globales
string g_commandsDir = "";
string g_responsesDir = "";
string g_bridgeDir = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
    // Configurar ruta del bridge
    if (BridgePath != "")
    {
        g_bridgeDir = BridgePath;
    }
    else
    {
        // Auto-detectar: usar carpeta del terminal + "MCP_Bridge"
        g_bridgeDir = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MCP_Bridge";
    }
    
    g_commandsDir = g_bridgeDir + "\\commands";
    g_responsesDir = g_bridgeDir + "\\responses";
    
    // Crear directorios
    FolderCreate(g_bridgeDir);
    FolderCreate(g_commandsDir);
    FolderCreate(g_responsesDir);
    
    Print("=== MCP Bridge EA Iniciado ===");
    Print("Bridge path: ", g_bridgeDir);
    Print("Cuenta: ", AccountNumber(), " @ ", AccountServer());
    Print("Balance: $", AccountBalance(), " Equity: $", AccountEquity());
    
    // Escribir archivo de estado
    WriteStatusFile();
    
    // Configurar timer para polling
    EventSetMillisecondTimer(PollInterval);
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
    Print("=== MCP Bridge EA Detenido ===");
}

//+------------------------------------------------------------------+
//| Timer function - Poll for commands                                  |
//+------------------------------------------------------------------+
void OnTimer()
{
    // Buscar archivos de comando
    string filter = g_commandsDir + "\\cmd_*.json";
    string filename = "";
    int handle = FileFindFirst(filter, filename);
    
    if (handle != INVALID_HANDLE)
    {
        do
        {
            string fullPath = g_commandsDir + "\\" + filename;
            ProcessCommand(fullPath, filename);
        }
        while (FileFindNext(handle, filename));
        
        FileFindClose(handle);
    }
}

//+------------------------------------------------------------------+
//| Process a command file                                              |
//+------------------------------------------------------------------+
void ProcessCommand(string filePath, string filename)
{
    // Leer comando
    int handle = FileOpen(filePath, FILE_READ | FILE_TXT | FILE_ANSI);
    if (handle == INVALID_HANDLE)
    {
        Print("Error abriendo comando: ", filePath);
        return;
    }
    
    string content = FileReadString(handle);
    FileClose(handle);
    
    if (content == "")
    {
        FileDelete(filePath);
        return;
    }
    
    // Parsear JSON (simplificado - MQL4 no tiene JSON nativo)
    string cmdId = ExtractJsonString(content, "id");
    string command = ExtractJsonString(content, "command");
    string params = ExtractJsonString(content, "params");
    
    Print("Procesando comando: ", command, " [", cmdId, "]");
    
    // Ejecutar comando
    string response = "";
    
    if (command == "ping")
        response = ExecutePing(cmdId);
    else if (command == "get_account_info")
        response = ExecuteGetAccountInfo(cmdId);
    else if (command == "get_positions")
        response = ExecuteGetPositions(cmdId, params);
    else if (command == "close_position")
        response = ExecuteClosePosition(cmdId, params);
    else if (command == "close_all_positions")
        response = ExecuteCloseAllPositions(cmdId, params);
    else if (command == "modify_position")
        response = ExecuteModifyPosition(cmdId, params);
    else if (command == "place_market_order")
        response = ExecutePlaceMarketOrder(cmdId, params);
    else if (command == "place_pending_order")
        response = ExecutePlacePendingOrder(cmdId, params);
    else if (command == "cancel_order")
        response = ExecuteCancelOrder(cmdId, params);
    else if (command == "cancel_all_orders")
        response = ExecuteCancelAllOrders(cmdId, params);
    else if (command == "get_orders")
        response = ExecuteGetOrders(cmdId, params);
    else if (command == "get_symbol_info")
        response = ExecuteGetSymbolInfo(cmdId, params);
    else if (command == "get_tick")
        response = ExecuteGetTick(cmdId, params);
    else if (command == "get_candles")
        response = ExecuteGetCandles(cmdId, params);
    else if (command == "get_symbols")
        response = ExecuteGetSymbols(cmdId, params);
    else
        response = CreateErrorResponse(cmdId, "Comando desconocido: " + command);
    
    // Escribir respuesta
    string respPath = g_responsesDir + "\\resp_" + cmdId + ".json";
    handle = FileOpen(respPath, FILE_WRITE | FILE_TXT | FILE_ANSI);
    if (handle != INVALID_HANDLE)
    {
        FileWriteString(handle, response);
        FileClose(handle);
    }
    
    // Eliminar archivo de comando
    FileDelete(filePath);
}

//+------------------------------------------------------------------+
//| JSON Helpers                                                        |
//+------------------------------------------------------------------+
string ExtractJsonString(string json, string key)
{
    string searchKey = "\"" + key + "\":";
    int pos = StringFind(json, searchKey);
    if (pos == -1)
        searchKey = "\"" + key + "\": ";
    pos = StringFind(json, searchKey);
    if (pos == -1) return "";
    
    pos += StringLen(searchKey);
    
    // Skip whitespace
    while (pos < StringLen(json) && StringGetCharacter(json, pos) == ' ')
        pos++;
    
    // Check if string value
    if (StringGetCharacter(json, pos) == '"')
    {
        pos++; // skip opening quote
        int endPos = StringFind(json, "\"", pos);
        if (endPos > pos)
            return StringSubstr(json, pos, endPos - pos);
    }
    
    // Numeric or other value
    int endPos = pos;
    while (endPos < StringLen(json))
    {
        ushort ch = StringGetCharacter(json, endPos);
        if (ch == ',' || ch == '}' || ch == ']')
            break;
        endPos++;
    }
    
    return StringSubstr(json, pos, endPos - pos);
}

double ExtractJsonDouble(string json, string key)
{
    string val = ExtractJsonString(json, key);
    if (val == "") return 0;
    return StringToDouble(val);
}

int ExtractJsonInt(string json, string key)
{
    string val = ExtractJsonString(json, key);
    if (val == "") return 0;
    return (int)StringToInteger(val);
}

string CreateResponse(string id, bool success, string data)
{
    return "{\"id\":\"" + id + "\",\"success\":" + (success ? "true" : "false") + 
           ",\"data\":" + data + ",\"timestamp\":" + IntegerToString((int)TimeCurrent()) + "}";
}

string CreateErrorResponse(string id, string error)
{
    return "{\"id\":\"" + id + "\",\"success\":false,\"error\":\"" + error + "\"}";
}

string JsonString(string key, string value)
{
    return "\"" + key + "\":\"" + value + "\"";
}

string JsonNumber(string key, double value)
{
    return "\"" + key + "\":" + DoubleToStr(value, 8);
}

string JsonInt(string key, int value)
{
    return "\"" + key + "\":" + IntegerToString(value);
}

string JsonBool(string key, bool value)
{
    return "\"" + key + "\":" + (value ? "true" : "false");
}

//+------------------------------------------------------------------+
//| Command Executors                                                   |
//+------------------------------------------------------------------+
string ExecutePing(string id)
{
    return CreateResponse(id, true, "{\"status\":\"ok\",\"broker\":\"mt4\",\"account\":" + 
           IntegerToString(AccountNumber()) + "}");
}

string ExecuteGetAccountInfo(string id)
{
    string data = "{" +
        JsonInt("login", AccountNumber()) + "," +
        JsonString("name", AccountName()) + "," +
        JsonString("server", AccountServer()) + "," +
        JsonNumber("balance", AccountBalance()) + "," +
        JsonNumber("equity", AccountEquity()) + "," +
        JsonNumber("margin", AccountMargin()) + "," +
        JsonNumber("free_margin", AccountFreeMargin()) + "," +
        JsonNumber("margin_level", AccountMargin() > 0 ? AccountEquity() / AccountMargin() * 100 : 0) + "," +
        JsonString("currency", AccountCurrency()) +
    "}";
    return CreateResponse(id, true, data);
}

string ExecuteGetPositions(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    string data = "[";
    int count = 0;
    
    int total = OrdersTotal();
    for (int i = 0; i < total; i++)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
        
        // Solo posiciones abiertas (no órdenes pendientes)
        if (OrderType() > OP_SELL)
            continue;
        
        // Filtrar por símbolo si se especifica
        if (symbol != "" && OrderSymbol() != symbol)
            continue;
        
        if (count > 0) data += ",";
        
        data += "{" +
            JsonInt("ticket", OrderTicket()) + "," +
            JsonString("symbol", OrderSymbol()) + "," +
            JsonString("type", OrderType() == OP_BUY ? "buy" : "sell") + "," +
            JsonNumber("volume", OrderLots()) + "," +
            JsonNumber("open_price", OrderOpenPrice()) + "," +
            JsonNumber("current_price", OrderType() == OP_BUY ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK)) + "," +
            JsonNumber("profit", OrderProfit()) + "," +
            JsonNumber("swap", OrderSwap()) + "," +
            JsonNumber("sl", OrderStopLoss()) + "," +
            JsonNumber("tp", OrderTakeProfit()) + "," +
            JsonInt("open_time", (int)OrderOpenTime()) + "," +
            JsonString("comment", OrderComment()) +
        "}";
        count++;
    }
    
    data += "]";
    return CreateResponse(id, true, data);
}

string ExecuteClosePosition(string id, string params)
{
    int ticket = ExtractJsonInt(params, "ticket");
    
    if (!OrderSelect(ticket, SELECT_BY_TICKET))
    {
        return CreateErrorResponse(id, "Posición " + IntegerToString(ticket) + " no encontrada");
    }
    
    double price = (OrderType() == OP_BUY) ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK);
    double profit = OrderProfit();
    
    bool result = OrderClose(ticket, OrderLots(), price, 30, clrNONE);
    
    if (result)
    {
        string data = "{" +
            JsonBool("success", true) + "," +
            JsonInt("ticket", ticket) + "," +
            JsonNumber("profit", profit) + "," +
            JsonNumber("price", price) +
        "}";
        return CreateResponse(id, true, data);
    }
    else
    {
        return CreateErrorResponse(id, "Error cerrando posición: " + IntegerToString(GetLastError()));
    }
}

string ExecuteCloseAllPositions(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    int closed = 0;
    string errors = "";
    
    for (int i = OrdersTotal() - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
        
        if (OrderType() > OP_SELL)
            continue;
        
        if (symbol != "" && OrderSymbol() != symbol)
            continue;
        
        double price = (OrderType() == OP_BUY) ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK);
        
        if (OrderClose(OrderTicket(), OrderLots(), price, 30, clrNONE))
        {
            closed++;
        }
        else
        {
            errors += "Ticket " + IntegerToString(OrderTicket()) + ": error " + IntegerToString(GetLastError()) + ";";
        }
    }
    
    string data = "{" +
        JsonBool("success", errors == "") + "," +
        JsonInt("closed_count", closed) + "," +
        JsonString("errors", errors) +
    "}";
    return CreateResponse(id, true, data);
}

string ExecuteModifyPosition(string id, string params)
{
    int ticket = ExtractJsonInt(params, "ticket");
    double sl = ExtractJsonDouble(params, "stop_loss");
    double tp = ExtractJsonDouble(params, "take_profit");
    
    if (!OrderSelect(ticket, SELECT_BY_TICKET))
    {
        return CreateErrorResponse(id, "Posición no encontrada");
    }
    
    // Si SL o TP son 0, mantener los actuales
    if (sl == 0) sl = OrderStopLoss();
    if (tp == 0) tp = OrderTakeProfit();
    
    bool result = OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrNONE);
    
    if (result)
    {
        return CreateResponse(id, true, "{" + JsonBool("success", true) + "," + JsonInt("ticket", ticket) + "}");
    }
    else
    {
        return CreateErrorResponse(id, "Error modificando: " + IntegerToString(GetLastError()));
    }
}

string ExecutePlaceMarketOrder(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    string orderType = ExtractJsonString(params, "order_type");
    double volume = ExtractJsonDouble(params, "volume");
    double sl = ExtractJsonDouble(params, "stop_loss");
    double tp = ExtractJsonDouble(params, "take_profit");
    string comment = ExtractJsonString(params, "comment");
    if (comment == "") comment = "MCP";
    
    int cmd = (orderType == "buy") ? OP_BUY : OP_SELL;
    double price = (cmd == OP_BUY) ? MarketInfo(symbol, MODE_ASK) : MarketInfo(symbol, MODE_BID);
    
    // Validar volumen mínimo
    double minLot = MarketInfo(symbol, MODE_MINLOT);
    double maxLot = MarketInfo(symbol, MODE_MAXLOT);
    volume = MathMax(minLot, MathMin(volume, maxLot));
    
    int ticket = OrderSend(symbol, cmd, volume, price, 30, sl, tp, comment, MagicNumber, 0, 
                           cmd == OP_BUY ? clrGreen : clrRed);
    
    if (ticket > 0)
    {
        string data = "{" +
            JsonBool("success", true) + "," +
            JsonInt("ticket", ticket) + "," +
            JsonNumber("volume", volume) + "," +
            JsonNumber("price", price) +
        "}";
        return CreateResponse(id, true, data);
    }
    else
    {
        return CreateErrorResponse(id, "Error abriendo orden: " + IntegerToString(GetLastError()));
    }
}

string ExecutePlacePendingOrder(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    string orderType = ExtractJsonString(params, "order_type");
    double volume = ExtractJsonDouble(params, "volume");
    double price = ExtractJsonDouble(params, "price");
    double sl = ExtractJsonDouble(params, "stop_loss");
    double tp = ExtractJsonDouble(params, "take_profit");
    
    int cmd = -1;
    if (orderType == "buy_limit") cmd = OP_BUYLIMIT;
    else if (orderType == "sell_limit") cmd = OP_SELLLIMIT;
    else if (orderType == "buy_stop") cmd = OP_BUYSTOP;
    else if (orderType == "sell_stop") cmd = OP_SELLSTOP;
    
    if (cmd == -1)
        return CreateErrorResponse(id, "Tipo de orden inválido: " + orderType);
    
    int ticket = OrderSend(symbol, cmd, volume, price, 30, sl, tp, "MCP Pending", MagicNumber, 0, clrBlue);
    
    if (ticket > 0)
    {
        string data = "{" +
            JsonBool("success", true) + "," +
            JsonInt("order_id", ticket) + "," +
            JsonNumber("price", price) +
        "}";
        return CreateResponse(id, true, data);
    }
    else
    {
        return CreateErrorResponse(id, "Error colocando orden: " + IntegerToString(GetLastError()));
    }
}

string ExecuteCancelOrder(string id, string params)
{
    int orderId = ExtractJsonInt(params, "order_id");
    
    if (!OrderSelect(orderId, SELECT_BY_TICKET))
    {
        return CreateErrorResponse(id, "Orden no encontrada");
    }
    
    if (OrderDelete(orderId))
    {
        return CreateResponse(id, true, "{" + JsonBool("success", true) + "," + JsonInt("order_id", orderId) + "}");
    }
    else
    {
        return CreateErrorResponse(id, "Error cancelando: " + IntegerToString(GetLastError()));
    }
}

string ExecuteCancelAllOrders(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    int cancelled = 0;
    
    for (int i = OrdersTotal() - 1; i >= 0; i--)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
        
        if (OrderType() <= OP_SELL)
            continue; // Skip posiciones abiertas
        
        if (symbol != "" && OrderSymbol() != symbol)
            continue;
        
        if (OrderDelete(OrderTicket()))
            cancelled++;
    }
    
    return CreateResponse(id, true, "{" + JsonInt("cancelled_count", cancelled) + "}");
}

string ExecuteGetOrders(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    string data = "[";
    int count = 0;
    
    string typeNames[] = {"buy", "sell", "buy_limit", "sell_limit", "buy_stop", "sell_stop"};
    
    for (int i = 0; i < OrdersTotal(); i++)
    {
        if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
            continue;
        
        // Solo órdenes pendientes
        if (OrderType() <= OP_SELL)
            continue;
        
        if (symbol != "" && OrderSymbol() != symbol)
            continue;
        
        if (count > 0) data += ",";
        
        string typeName = "";
        if (OrderType() >= 0 && OrderType() <= 5)
            typeName = typeNames[OrderType()];
        else
            typeName = "type_" + IntegerToString(OrderType());
        
        data += "{" +
            JsonInt("ticket", OrderTicket()) + "," +
            JsonString("symbol", OrderSymbol()) + "," +
            JsonString("type", typeName) + "," +
            JsonNumber("volume", OrderLots()) + "," +
            JsonNumber("price", OrderOpenPrice()) + "," +
            JsonNumber("sl", OrderStopLoss()) + "," +
            JsonNumber("tp", OrderTakeProfit()) + "," +
            JsonInt("time", (int)OrderOpenTime()) +
        "}";
        count++;
    }
    
    data += "]";
    return CreateResponse(id, true, data);
}

string ExecuteGetSymbolInfo(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    
    string data = "{" +
        JsonString("name", symbol) + "," +
        JsonInt("spread", (int)MarketInfo(symbol, MODE_SPREAD)) + "," +
        JsonInt("digits", (int)MarketInfo(symbol, MODE_DIGITS)) + "," +
        JsonNumber("point", MarketInfo(symbol, MODE_POINT)) + "," +
        JsonNumber("tick_size", MarketInfo(symbol, MODE_TICKSIZE)) + "," +
        JsonNumber("contract_size", MarketInfo(symbol, MODE_LOTSIZE)) + "," +
        JsonNumber("min_lot", MarketInfo(symbol, MODE_MINLOT)) + "," +
        JsonNumber("max_lot", MarketInfo(symbol, MODE_MAXLOT)) + "," +
        JsonNumber("lot_step", MarketInfo(symbol, MODE_LOTSTEP)) + "," +
        JsonNumber("swap_long", MarketInfo(symbol, MODE_SWAPLONG)) + "," +
        JsonNumber("swap_short", MarketInfo(symbol, MODE_SWAPSHORT)) +
    "}";
    return CreateResponse(id, true, data);
}

string ExecuteGetTick(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    
    double bid = MarketInfo(symbol, MODE_BID);
    double ask = MarketInfo(symbol, MODE_ASK);
    
    string data = "{" +
        JsonString("symbol", symbol) + "," +
        JsonInt("time", (int)TimeCurrent()) + "," +
        JsonNumber("bid", bid) + "," +
        JsonNumber("ask", ask) + "," +
        JsonNumber("last", bid) + "," +
        JsonInt("volume", (int)MarketInfo(symbol, MODE_VOLUME)) + "," +
        JsonNumber("spread", ask - bid) +
    "}";
    return CreateResponse(id, true, data);
}

string ExecuteGetCandles(string id, string params)
{
    string symbol = ExtractJsonString(params, "symbol");
    string timeframe = ExtractJsonString(params, "timeframe");
    int count = ExtractJsonInt(params, "count");
    if (count <= 0 || count > 500) count = 100;
    
    // Mapear timeframe string a constante MT4
    int tf = PERIOD_H1; // default
    if (timeframe == "M1") tf = PERIOD_M1;
    else if (timeframe == "M5") tf = PERIOD_M5;
    else if (timeframe == "M15") tf = PERIOD_M15;
    else if (timeframe == "M30") tf = PERIOD_M30;
    else if (timeframe == "H1") tf = PERIOD_H1;
    else if (timeframe == "H4") tf = PERIOD_H4;
    else if (timeframe == "D1") tf = PERIOD_D1;
    else if (timeframe == "W1") tf = PERIOD_W1;
    else if (timeframe == "MN1") tf = PERIOD_MN1;
    
    string data = "[";
    int actualCount = MathMin(count, iBars(symbol, tf));
    
    for (int i = actualCount - 1; i >= 0; i--)
    {
        if (i < actualCount - 1) data += ",";
        
        data += "{" +
            JsonInt("time", (int)iTime(symbol, tf, i)) + "," +
            JsonNumber("open", iOpen(symbol, tf, i)) + "," +
            JsonNumber("high", iHigh(symbol, tf, i)) + "," +
            JsonNumber("low", iLow(symbol, tf, i)) + "," +
            JsonNumber("close", iClose(symbol, tf, i)) + "," +
            JsonInt("tick_volume", (int)iVolume(symbol, tf, i)) + "," +
            "0,0" +  // spread, real_volume (no disponibles en MT4)
        "}";
    }
    
    data += "]";
    return CreateResponse(id, true, data);
}

string ExecuteGetSymbols(string id, string params)
{
    // MT4 no tiene una forma fácil de listar todos los símbolos
    // Retornar los más comunes
    string data = "[";
    string commonSymbols[] = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURGBP", "EURJPY", "GBPJPY", "XAUUSD", "XAGUSD", "US30", "SPX500",
        "NAS100", "BTCUSD", "ETHUSD"
    };
    
    for (int i = 0; i < ArraySize(commonSymbols); i++)
    {
        if (i > 0) data += ",";
        data += "\"" + commonSymbols[i] + "\"";
    }
    
    data += "]";
    return CreateResponse(id, true, data);
}

//+------------------------------------------------------------------+
//| Write status file                                                   |
//+------------------------------------------------------------------+
void WriteStatusFile()
{
    string statusPath = g_bridgeDir + "\\status.json";
    int handle = FileOpen(statusPath, FILE_WRITE | FILE_TXT | FILE_ANSI);
    if (handle != INVALID_HANDLE)
    {
        string status = "{" +
            JsonBool("running", true) + "," +
            JsonInt("account", AccountNumber()) + "," +
            JsonString("server", AccountServer()) + "," +
            JsonNumber("balance", AccountBalance()) + "," +
            JsonNumber("equity", AccountEquity()) + "," +
            JsonString("broker", "mt4") + "," +
            JsonInt("timestamp", (int)TimeCurrent()) +
        "}";
        FileWriteString(handle, status);
        FileClose(handle);
    }
}
//+------------------------------------------------------------------+
