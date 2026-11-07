internal class GwaiNetWeights
{
   private static var _weightsByCheckpoint:Object = {};
   private static var _stepsByCheckpoint:Object = {};

   public static function load(data:Object) : Vector.<Number>
   {
      if(data == null || !data.EMBEDDED) throw new Error("no exported policy weights are embedded");
      var checkpoint:String = String(data.CHECKPOINT_SHA256);
      if(_weightsByCheckpoint[checkpoint] != null) return _weightsByCheckpoint[checkpoint];
      var result:Vector.<Number> = new Vector.<Number>(data.WEIGHT_COUNT,true);
      var cursor:int = 0;
      for(var chunkIndex:int = 0; chunkIndex < data.BASE64.length; chunkIndex++)
      {
         var bytes:flash.utils.ByteArray = decodeBase64(String(data.BASE64[chunkIndex]));
         bytes.endian = flash.utils.Endian.LITTLE_ENDIAN;
         bytes.position = 0;
         while(bytes.bytesAvailable >= 4)
         {
            result[cursor++] = bytes.readFloat();
         }
      }
      if(cursor != data.WEIGHT_COUNT)
         throw new Error("embedded policy weight count does not match its header");
      _weightsByCheckpoint[checkpoint] = result;
      return result;
   }

   public static function isLoaded(data:Object) : Boolean
   {
      return data != null && _weightsByCheckpoint[String(data.CHECKPOINT_SHA256)] != null;
   }

   // The same decode as load, a few chunks per call, so a network can be decoded across many
   // ticks before it is needed. True once the weights are in load's cache; load then returns them.
   public static function loadStep(data:Object, chunks:int) : Boolean
   {
      if(data == null || !data.EMBEDDED) throw new Error("no exported policy weights are embedded");
      var checkpoint:String = String(data.CHECKPOINT_SHA256);
      if(_weightsByCheckpoint[checkpoint] != null)
      {
         delete _stepsByCheckpoint[checkpoint];
         return true;
      }
      var step:Object = _stepsByCheckpoint[checkpoint];
      if(step == null)
      {
         step = {result:new Vector.<Number>(data.WEIGHT_COUNT,true),cursor:0,chunk:0};
         _stepsByCheckpoint[checkpoint] = step;
      }
      var result:Vector.<Number> = step.result;
      var cursor:int = int(step.cursor);
      var stop:int = Math.min(int(data.BASE64.length),int(step.chunk) + Math.max(1,chunks));
      for(var chunkIndex:int = int(step.chunk); chunkIndex < stop; chunkIndex++)
      {
         var bytes:flash.utils.ByteArray = decodeBase64(String(data.BASE64[chunkIndex]));
         bytes.endian = flash.utils.Endian.LITTLE_ENDIAN;
         bytes.position = 0;
         while(bytes.bytesAvailable >= 4)
         {
            result[cursor++] = bytes.readFloat();
         }
      }
      step.cursor = cursor;
      step.chunk = stop;
      if(stop < int(data.BASE64.length)) return false;
      delete _stepsByCheckpoint[checkpoint];
      if(cursor != data.WEIGHT_COUNT)
         throw new Error("embedded policy weight count does not match its header");
      _weightsByCheckpoint[checkpoint] = result;
      return true;
   }

   private static function decodeBase64(value:String) : flash.utils.ByteArray
   {
      var alphabet:String = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
      var bytes:flash.utils.ByteArray = new flash.utils.ByteArray();
      for(var index:int = 0; index < value.length; index += 4)
      {
         var a:int = alphabet.indexOf(value.charAt(index));
         var b:int = alphabet.indexOf(value.charAt(index + 1));
         var c:int = value.charAt(index + 2) == "=" ? 0 : alphabet.indexOf(value.charAt(index + 2));
         var d:int = value.charAt(index + 3) == "=" ? 0 : alphabet.indexOf(value.charAt(index + 3));
         if(a < 0 || b < 0 || c < 0 || d < 0) throw new Error("invalid embedded base64 weight data");
         bytes.writeByte((a << 2) | (b >> 4));
         if(value.charAt(index + 2) != "=") bytes.writeByte(((b & 15) << 4) | (c >> 2));
         if(value.charAt(index + 3) != "=") bytes.writeByte(((c & 3) << 6) | d);
      }
      return bytes;
   }
}
