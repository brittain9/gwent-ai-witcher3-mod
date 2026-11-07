// Buffers a bounded match's timing JSON records in the Witcher 3 user-settings path.
@addField( CR4GwintGameMenu ) var gwaiTimingProbeIndex : int;

function GwaiTimingProbeKey( index : int ) : string
{
	var value : string = IntToString( index );
	while ( StrLen( value ) < 6 )
	{
		value = "0" + value;
	}
	return "record_" + value;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentTimingBegin()
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	gwaiTimingProbeIndex = 0;
	config.SetRawConfigValueByStr( "W3GwentTimingProbe", "count", "0" );
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentTimingRecord( record : string )
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	if ( gwaiTimingProbeIndex >= 255 )
	{
		return false;
	}
	config.SetRawConfigValueByStr( "W3GwentTimingProbe", GwaiTimingProbeKey( gwaiTimingProbeIndex ), record );
	gwaiTimingProbeIndex += 1;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentTimingFinish( summary : string )
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	config.SetRawConfigValueByStr( "W3GwentTimingProbe", GwaiTimingProbeKey( gwaiTimingProbeIndex ), summary );
	gwaiTimingProbeIndex += 1;
	config.SetRawConfigValueByStr( "W3GwentTimingProbe", "count", IntToString( gwaiTimingProbeIndex ) );
	theGame.SaveUserSettings();
}
