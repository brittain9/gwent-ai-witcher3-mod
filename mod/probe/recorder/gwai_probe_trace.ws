// Stores the AS3 probe's JSON records without parsing or rounding numeric fields.
// The JSON is trace-only. This script exposes no route for returning decisions to AS3.

@addField( CR4GwintGameMenu ) var gwaiProbeTraceIndex : int;
@addField( CR4GwintGameMenu ) var gwaiProbeTraceIndexLoaded : bool;

function GwaiProbeTraceKey( index : int ) : string
{
	var value : string = IntToString( index );
	while ( StrLen( value ) < 6 )
	{
		value = "0" + value;
	}
	return "trace_" + value;
}

@addMethod( CR4GwintGameMenu ) event OnGwaiProbeTrace( trace : string )
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	if ( !gwaiProbeTraceIndexLoaded )
	{
		gwaiProbeTraceIndex = StringToInt( config.GetRawConfigValueByStr( "GwaiProbeTrace", "count" ), 0 );
		if ( gwaiProbeTraceIndex < 0 )
		{
			gwaiProbeTraceIndex = 0;
		}
		gwaiProbeTraceIndexLoaded = true;
	}
	config.SetRawConfigValueByStr( "GwaiProbeTrace", GwaiProbeTraceKey( gwaiProbeTraceIndex ), trace );
	gwaiProbeTraceIndex += 1;
	config.SetRawConfigValueByStr( "GwaiProbeTrace", "count", IntToString( gwaiProbeTraceIndex ) );
	theGame.SaveUserSettings();
}
