//+------------------------------------------------------------------+
//|  CGG v16.0 MILLIONAIRE — Full Bridge + 5 Pro Features             |
//|  1. Trailing Stop (never let winner become loser)                 |
//|  2. Breakeven (zero risk after +$0.40)                           |
//|  3. Session Filter (London + NY only)                            |
//|  4. Smart Martingale (+50% lot after loss, max 3)                |
//|  5. Dynamic TP/SL by ATR volatility                              |
//+------------------------------------------------------------------+
#property copyright "CGG v16.0"
#property version "16.00"
#property strict
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
double gTP=8.0, gSL=3.0, gOffset=3.0, gMinR=4.0, gMaxS=3.0;
double gStealthR=3.0;
int gStealthCool=120;
input double InpTP=8.0,InpSL=3.0,InpOff=3.0,InpMinR=4.0,InpMaxS=3.0;
input double InpStealthR=3.0;
input int InpStealthCool=120,InpMagic=20240027;
CTrade t;CSymbolInfo s;
string gS;double gP,gML,gLS;int gD;
datetime gLT,gHideUntil;int gW,gL;double gSt,gTot;
int gDir,gBuy,gSell,gLoss,gWin,gLossStreak;
double gLast;ulong gTicket;
double gTrailStop=0.30;    // Trailing: cuando +$0.30, SL a +$0.10
double gBreakeven=0.40;    // Breakeven: cuando +$0.40, SL a $0.00
bool gSessionFilter=true;  // Solo operar sesiones activas
bool gMartingale=true;     // Smart martingale activado

int OnInit(){gS=_Symbol;if(!s.Name(gS))return INIT_FAILED;
gD=(int)SymbolInfoInteger(gS,SYMBOL_DIGITS);
double pt=SymbolInfoDouble(gS,SYMBOL_POINT);gP=(gD==5||gD==3)?pt*10:pt;
gML=SymbolInfoDouble(gS,SYMBOL_VOLUME_MIN);gLS=SymbolInfoDouble(gS,SYMBOL_VOLUME_STEP);
t.SetExpertMagicNumber(InpMagic);t.SetTypeFillingBySymbol(gS);t.SetDeviationInPoints(200);
gSt=ACCOUNT_BALANCE;gLossStreak=0;
gTP=InpTP; gSL=InpSL; gOffset=InpOff; gMinR=InpMinR; gMaxS=InpMaxS;
gStealthR=InpStealthR; gStealthCool=InpStealthCool;
Print("CGG v16.0 MILLIONAIRE | TP",gTP,":SL",gSL," | Trail:",gTrailStop," | BE:",gBreakeven," | $",DoubleToString(gSt,2));
return INIT_SUCCEEDED;}

// --- LOTE DINAMICO + SMART MARTINGALE ---
double GetLotSize()
{
   double bal=AccountInfoDouble(ACCOUNT_BALANCE);
   double lot=0.01;
   if(bal>=200)lot=0.08;else if(bal>=150)lot=0.06;else if(bal>=100)lot=0.04;
   else if(bal>=75)lot=0.03;else if(bal>=50)lot=0.02;
   // Smart martingale: +50% tras cada loss, max 3 niveles
   if(gMartingale && gLossStreak>0 && gLossStreak<=3)
      lot=lot*(1.0+0.5*gLossStreak);
   // Normalizar al step del broker
   lot=MathRound(lot/gLS)*gLS;
   if(lot<gML)lot=gML;
   return lot;
}

// --- FILTRO DE SESION (solo horas con volumen) ---
bool IsSessionActive()
{
   if(!gSessionFilter) return true;
   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   int hour=dt.hour;
   // GMT approximation: London 3-11am EST, NY 8am-5pm EST
   // Operar 3am-5pm EST (8-22 GMT approx)
   // Para servidor tastyfx (GMT+0 approx):
   if(hour>=7 && hour<=21) return true;  // London+NY overlap
   return false;
}

// --- TRAILING STOP + BREAKEVEN ---
void ManagePosition(double prof)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ut=PositionGetTicket(i);
      if(!ut||!PositionSelectByTicket(ut))continue;
      if(PositionGetString(POSITION_SYMBOL)!=gS)continue;
      
      double posProf=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
      long type=PositionGetInteger(POSITION_TYPE);
      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL=PositionGetDouble(POSITION_SL);
      double curTP=PositionGetDouble(POSITION_TP);
      double bid=SymbolInfoDouble(gS,SYMBOL_BID);
      double ask=SymbolInfoDouble(gS,SYMBOL_ASK);
      
      // BREAKEVEN: si profit >= gBreakeven, SL a precio de entrada
      if(posProf>=gBreakeven && curSL==0)
      {
         if(type==POSITION_TYPE_BUY) t.PositionModify(ut,openPrice,curTP);
         else t.PositionModify(ut,openPrice,curTP);
      }
      // TRAILING: si profit >= gTrailStop, SL a +$0.10
      else if(posProf>=gTrailStop)
      {
         double trailPrice;
         if(type==POSITION_TYPE_BUY)
         {
            trailPrice=openPrice+1.0*gP; // +1 pip sobre entrada
            if(curSL==0 || trailPrice>curSL) t.PositionModify(ut,trailPrice,curTP);
         }
         else
         {
            trailPrice=openPrice-1.0*gP;
            if(curSL==0 || trailPrice<curSL) t.PositionModify(ut,trailPrice,curTP);
         }
      }
   }
}

void OnTick()
{
   HermesBridge();
   s.RefreshRates();
   double spr=(s.Ask()-s.Bid())/gP;
   if(spr>gMaxS)return;
   
   int cnt=0;double prof=0;
   for(int i=PositionsTotal()-1;i>=0;i--){ulong ut=PositionGetTicket(i);if(!ut||!PositionSelectByTicket(ut))continue;if(PositionGetString(POSITION_SYMBOL)!=gS)continue;cnt++;prof+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);}
   
   // Trailing + Breakeven SIEMPRE
   if(cnt>0) ManagePosition(prof);
   
   // TP
   if(cnt>0&&prof>=gTP*0.10){CloseAll();gW++;gTot+=prof;gLast=prof;gLoss=0;gWin=gDir;gLossStreak=0;if(gDir>0)gBuy=1;else gSell=1;gTicket=0;Print("WIN $",DoubleToString(prof,2)," | Total:$",DoubleToString(gTot,2)," | W:",gW," L:",gL);return;}
   // SL
   if(cnt>0&&prof<=-gSL*0.10){CloseAll();gL++;gTot+=prof;gLast=prof;gLoss++;gLossStreak++;gWin=0;if(gDir>0)gBuy=-1;else gSell=-1;gTicket=0;Print("LOSS $",DoubleToString(prof,2)," | Streak:",gLossStreak," | W:",gW," L:",gL);return;}
   
   double rng=(iHigh(gS,PERIOD_M5,1)-iLow(gS,PERIOD_M5,1))/gP;
   double avgRng=0;for(int j=1;j<=5;j++)avgRng+=(iHigh(gS,PERIOD_M5,j)-iLow(gS,PERIOD_M5,j))/gP;avgRng/=5.0;
   
   // ATR dinamico: ajustar TP segun volatilidad
   int hAtr=iATR(gS,PERIOD_M5,14);
   double atrBuf[1]; double atrPips=5.0;
   if(CopyBuffer(hAtr,0,1,1,atrBuf)>0) atrPips=atrBuf[0]/gP;
   IndicatorRelease(hAtr);
   // TP adaptativo: si alta volatilidad, TP mas grande
   if(atrPips>8) gTP=InpTP*1.5; else gTP=InpTP;
   
   if(cnt==0&&gTicket!=0&&(rng<gStealthR||avgRng<gMinR)){if(TimeCurrent()>gHideUntil){t.OrderDelete(gTicket);gTicket=0;}return;}
   
   // FILTRO DE SESION
   if(cnt==0&&gTicket==0&&TimeCurrent()-gLT>5&&TimeCurrent()>=gHideUntil)
   {
      if(!IsSessionActive()) return; // No operar fuera de sesion
      if(avgRng>=gMinR&&rng>=gStealthR){
         int h9=iMA(gS,PERIOD_M5,9,0,MODE_EMA,PRICE_CLOSE);int h21=iMA(gS,PERIOD_M5,21,0,MODE_EMA,PRICE_CLOSE);
         double e9[1],e21[1],ema9=0,ema21=0;
         if(CopyBuffer(h9,0,1,1,e9)>0)ema9=e9[0];if(CopyBuffer(h21,0,1,1,e21)>0)ema21=e21[0];
         IndicatorRelease(h9);IndicatorRelease(h21);
         
         // RSI filter: no comprar sobrecomprado, no vender sobrevendido
         int hRsi=iRSI(gS,PERIOD_M5,14,PRICE_CLOSE);
         double rsiBuf[1]; double rsi=50;
         if(CopyBuffer(hRsi,0,1,1,rsiBuf)>0) rsi=rsiBuf[0];
         IndicatorRelease(hRsi);
         
         int tr=ema9>=ema21?1:-1,d=0;
         // RSI filter
         if(tr>0 && rsi>70) {Print("RSI sobrecomprado ",rsi," - skip BUY"); return;}
         if(tr<0 && rsi<30) {Print("RSI sobrevendido ",rsi," - skip SELL"); return;}
         
         if(gWin!=0&&gLoss==0)d=gWin;
         else if(gLossStreak>=2&&gDir!=0){d=-gDir;Print("ANTI-CHOQUE streak:",gLossStreak);}
         else if(gLast<0&&gDir!=0)d=-gDir;
         else if(gBuy==-1&&gSell!=-1)d=-1;else if(gSell==-1&&gBuy!=-1)d=1;
         else if(gBuy==-1&&gSell==-1){gBuy=0;gSell=0;d=tr;}else d=tr;
         
         if(d!=0){double pr=d>0?s.Bid()-gOffset*gP:s.Ask()+gOffset*gP;
            double lot=GetLotSize();
            if(d>0)gTicket=t.Buy(lot,gS,pr,0,0,"CGG16");else gTicket=t.Sell(lot,gS,pr,0,0,"CGG16");
            if(gTicket>0){gDir=d;gLT=TimeCurrent();Print(d>0?"BUY":"SELL"," #",gTicket," lot:",lot," @",DoubleToString(pr,gD)," | RSI:",DoubleToString(rsi,1)," | ATR:",DoubleToString(atrPips,1),"p");}}}}
}

void CloseAll(){for(int a=0;a<5;a++){for(int i=PositionsTotal()-1;i>=0;i--){ulong ut=PositionGetTicket(i);if(!ut||!PositionSelectByTicket(ut))continue;if(PositionGetString(POSITION_SYMBOL)!=gS)continue;t.PositionClose(ut);}int c=0;for(int i=PositionsTotal()-1;i>=0;i--){ulong ut=PositionGetTicket(i);if(ut&&PositionSelectByTicket(ut)&&PositionGetString(POSITION_SYMBOL)==gS)c++;}if(c==0)break;Sleep(50);}}

ENUM_TIMEFRAMES ParseTF(string tfStr){if(tfStr=="M1")return PERIOD_M1;else if(tfStr=="M5")return PERIOD_M5;else if(tfStr=="M15")return PERIOD_M15;else if(tfStr=="M30")return PERIOD_M30;else if(tfStr=="H1")return PERIOD_H1;else if(tfStr=="H4")return PERIOD_H4;else if(tfStr=="D1")return PERIOD_D1;else if(tfStr=="W1")return PERIOD_W1;return PERIOD_M5;}

void HermesBridge(){
   static datetime lastRead=0;static int holdCycles=0;
   if(TimeCurrent()-lastRead<2)return;lastRead=TimeCurrent();
   bool wroteResponse=false;
   string cmd="";int hc=FileOpen("PythonBridge\\orders.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(hc!=INVALID_HANDLE){if(!FileIsEnding(hc))cmd=FileReadString(hc);FileClose(hc);StringTrimLeft(cmd);StringTrimRight(cmd);}
   
   if(StringFind(cmd,"ORDER|")==0){string parts[];StringSplit(cmd,'|',parts);if(ArraySize(parts)>=3){MqlTick tk;SymbolInfoTick(gS,tk);double lot=StringToDouble(parts[2]);if(parts[1]=="BUY")t.Buy(lot,gS,tk.ask,0,0,"Hermes");else t.Sell(lot,gS,tk.bid,0,0,"Hermes");}}
   else if(cmd=="CLOSEALL"||cmd=="CANCELALL"){for(int i=OrdersTotal()-1;i>=0;i--){ulong ot=OrderGetTicket(i);if(ot)t.OrderDelete(ot);}CloseAll();}
   else if(StringFind(cmd,"CONFIG|")==0){string parts[];StringSplit(cmd,'|',parts);if(ArraySize(parts)>=3){string key=parts[1];double val=StringToDouble(parts[2]);if(key=="TP")gTP=val;else if(key=="SL")gSL=val;else if(key=="OFFSET")gOffset=val;else if(key=="MINR")gMinR=val;else if(key=="MAXS")gMaxS=val;else if(key=="STEALTH")gStealthR=val;else if(key=="COOL")gStealthCool=(int)val;else if(key=="TRAIL")gTrailStop=val;else if(key=="BE")gBreakeven=val;else if(key=="MARTINGALE"){gMartingale=(val>0);}else if(key=="SESSION"){gSessionFilter=(val>0);}Print("CONFIG ",key,"=",val);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,StringFormat("CONFIG|OK|%s=%.2f",key,val));FileClose(h);wroteResponse=true;}}}
   else if(StringFind(cmd,"PRICE|")==0){string parts[];StringSplit(cmd,'|',parts);string sym=(ArraySize(parts)>1&&parts[1]!="")?parts[1]:gS;if(!SymbolSelect(sym,true))sym=gS;MqlTick tk;if(SymbolInfoTick(sym,tk)){int d=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);double pt=SymbolInfoDouble(sym,SYMBOL_POINT);double spread=(tk.ask-tk.bid)/((d==3||d==5)?pt*10:pt);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,StringFormat("PRICE|%s|%.5f|%.5f|%.1f|%d",sym,tk.bid,tk.ask,spread,d));FileClose(h);wroteResponse=true;}}}
   else if(StringFind(cmd,"RATES|")==0){string parts[];StringSplit(cmd,'|',parts);string sym=(ArraySize(parts)>1&&parts[1]!="")?parts[1]:gS;string tfStr=(ArraySize(parts)>2)?parts[2]:"M5";int count=(ArraySize(parts)>3)?(int)StringToInteger(parts[3]):50;if(count<1)count=1;if(count>200)count=200;if(!SymbolSelect(sym,true))sym=gS;MqlRates rates[];ArraySetAsSeries(rates,true);int copied=CopyRates(sym,ParseTF(tfStr),0,count,rates);if(copied>0){string result=StringFormat("RATES|%s|%s|%d",sym,tfStr,copied);for(int i=copied-1;i>=0;i--)result+=StringFormat("|%d,%.5f,%.5f,%.5f,%.5f,%d",(int)rates[i].time,rates[i].open,rates[i].high,rates[i].low,rates[i].close,(int)rates[i].tick_volume);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,result);FileClose(h);wroteResponse=true;}}}
   else if(StringFind(cmd,"RSI|")==0){string parts[];StringSplit(cmd,'|',parts);string sym=(ArraySize(parts)>1&&parts[1]!="")?parts[1]:gS;string tfStr=(ArraySize(parts)>2)?parts[2]:"M5";int period=(ArraySize(parts)>3)?(int)StringToInteger(parts[3]):14;if(!SymbolSelect(sym,true))sym=gS;int hdl=iRSI(sym,ParseTF(tfStr),period,PRICE_CLOSE);if(hdl!=INVALID_HANDLE){double buf[];ArraySetAsSeries(buf,true);if(CopyBuffer(hdl,0,0,50,buf)>0){string r=StringFormat("RSI|%s|%s|%d",sym,tfStr,period);for(int i=0;i<50;i++)r+=StringFormat("|%.2f",buf[i]);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,r);FileClose(h);wroteResponse=true;}}IndicatorRelease(hdl);}}
   else if(StringFind(cmd,"EMA|")==0){string parts[];StringSplit(cmd,'|',parts);string sym=(ArraySize(parts)>1&&parts[1]!="")?parts[1]:gS;string tfStr=(ArraySize(parts)>2)?parts[2]:"M5";int period=(ArraySize(parts)>3)?(int)StringToInteger(parts[3]):9;if(!SymbolSelect(sym,true))sym=gS;int hdl=iMA(sym,ParseTF(tfStr),period,0,MODE_EMA,PRICE_CLOSE);if(hdl!=INVALID_HANDLE){double buf[];ArraySetAsSeries(buf,true);if(CopyBuffer(hdl,0,0,50,buf)>0){string r=StringFormat("EMA|%s|%s|%d",sym,tfStr,period);for(int i=0;i<50;i++)r+=StringFormat("|%.5f",buf[i]);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,r);FileClose(h);wroteResponse=true;}}IndicatorRelease(hdl);}}
   else if(StringFind(cmd,"ATR|")==0){string parts[];StringSplit(cmd,'|',parts);string sym=(ArraySize(parts)>1&&parts[1]!="")?parts[1]:gS;string tfStr=(ArraySize(parts)>2)?parts[2]:"M5";int period=(ArraySize(parts)>3)?(int)StringToInteger(parts[3]):14;if(!SymbolSelect(sym,true))sym=gS;int hdl=iATR(sym,ParseTF(tfStr),period);if(hdl!=INVALID_HANDLE){double buf[];ArraySetAsSeries(buf,true);if(CopyBuffer(hdl,0,0,50,buf)>0){string r=StringFormat("ATR|%s|%s|%d",sym,tfStr,period);for(int i=0;i<50;i++)r+=StringFormat("|%.5f",buf[i]);int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,r);FileClose(h);wroteResponse=true;}}IndicatorRelease(hdl);}}
   else if(cmd=="SYMBOLS"){int total=SymbolsTotal(false);string result="SYMBOLS|"+IntegerToString(total);for(int i=0;i<total&&i<100;i++){string s=SymbolName(i,false);if(s!="")result+="|"+s;}int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,result);FileClose(h);wroteResponse=true;}}
   
   if(cmd!=""){hc=FileOpen("PythonBridge\\orders.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(hc!=INVALID_HANDLE){FileWriteString(hc,"");FileClose(hc);}}
   
   if(!wroteResponse&&holdCycles<=0){int h=FileOpen("PythonBridge\\results.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);if(h!=INVALID_HANDLE){FileWriteString(h,StringFormat("ACCOUNT|%I64d|%.2f|%.2f|%.2f|%.2f|%.2f|%I64d|%s|%s",AccountInfoInteger(ACCOUNT_LOGIN),AccountInfoDouble(ACCOUNT_BALANCE),AccountInfoDouble(ACCOUNT_EQUITY),AccountInfoDouble(ACCOUNT_MARGIN),AccountInfoDouble(ACCOUNT_MARGIN_FREE),AccountInfoDouble(ACCOUNT_PROFIT),AccountInfoInteger(ACCOUNT_LEVERAGE),AccountInfoString(ACCOUNT_CURRENCY),AccountInfoString(ACCOUNT_SERVER)));FileClose(h);}}
   if(wroteResponse)holdCycles=3;else if(holdCycles>0)holdCycles--;
}
