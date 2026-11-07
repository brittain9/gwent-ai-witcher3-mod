// Local-only recorder sink. It stores JSONL bytes in numbered settings chunks.
@addField( CR4GwintGameMenu ) var gwaiRecorderMatchId : int;
@addField( CR4GwintGameMenu ) var gwaiRecorderChunkIndex : int;
@addField( CR4GwintGameMenu ) var gwaiRecorderBuffer : string;
@addField( CR4GwintGameMenu ) var gwaiRecorderActive : bool;

function GwaiRecorderPad( value : int ) : string
{
	var result : string = IntToString( value );
	while ( StrLen( result ) < 6 )
	{
		result = "0" + result;
	}
	return result;
}

@addMethod( CR4GwintGameMenu ) function gwaiRecorderFlush( finalChunk : bool ) : void
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	var prefix : string = "m" + GwaiRecorderPad( gwaiRecorderMatchId ) + "_";
	var part : string;
	var wroteChunk : bool = false;
	while ( StrLen( gwaiRecorderBuffer ) > 780 || finalChunk && StrLen( gwaiRecorderBuffer ) > 0 )
	{
		if ( StrLen( gwaiRecorderBuffer ) > 780 )
		{
			part = StrMid( gwaiRecorderBuffer, 0, 780 );
			gwaiRecorderBuffer = StrMid( gwaiRecorderBuffer, 780, StrLen( gwaiRecorderBuffer ) - 780 );
		}
		else
		{
			part = gwaiRecorderBuffer;
			gwaiRecorderBuffer = "";
		}
		config.SetRawConfigValueByStr( "W3GwentRecorder", prefix + GwaiRecorderPad( gwaiRecorderChunkIndex ), part );
		gwaiRecorderChunkIndex += 1;
		config.SetRawConfigValueByStr( "W3GwentRecorder", prefix + "count", IntToString( gwaiRecorderChunkIndex ) );
		wroteChunk = true;
	}
	if ( finalChunk || wroteChunk )
	{
		theGame.SaveUserSettings();
	}
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentRecorderStart()
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	var nextMatch : int = StringToInt( config.GetRawConfigValueByStr( "W3GwentRecorder", "next_match" ), 0 ) + 1;
	if ( gwaiRecorderActive )
	{
		gwaiRecorderFlush( true );
		config.SetRawConfigValueByStr( "W3GwentRecorder", "m" + GwaiRecorderPad( gwaiRecorderMatchId ) + "_status", "interrupted" );
	}
	gwaiRecorderMatchId = nextMatch;
	gwaiRecorderChunkIndex = 0;
	gwaiRecorderBuffer = "";
	gwaiRecorderActive = true;
	config.SetRawConfigValueByStr( "W3GwentRecorder", "next_match", IntToString( nextMatch ) );
	config.SetRawConfigValueByStr( "W3GwentRecorder", "active_match", "m" + GwaiRecorderPad( nextMatch ) );
	theGame.SaveUserSettings();
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentRecorderRecord( record : string )
{
	if ( !gwaiRecorderActive )
	{
		return false;
	}
	if ( StrLen( gwaiRecorderBuffer ) + StrLen( record ) + 1 > 780 )
	{
		gwaiRecorderFlush( false );
	}
	gwaiRecorderBuffer += record + "\n";
	gwaiRecorderFlush( false );
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentRecorderEnd( reason : string )
{
	var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
	if ( !gwaiRecorderActive )
	{
		return false;
	}
	gwaiRecorderFlush( true );
	config.SetRawConfigValueByStr( "W3GwentRecorder", "m" + GwaiRecorderPad( gwaiRecorderMatchId ) + "_status", reason );
	config.SetRawConfigValueByStr( "W3GwentRecorder", "active_match", "" );
	gwaiRecorderActive = false;
	theGame.SaveUserSettings();
}
