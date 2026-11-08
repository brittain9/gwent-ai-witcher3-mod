internal class GwaiNetWeights
{
   private static var _weightsByCheckpoint:Object = {};
   private static var _stepsByCheckpoint:Object = {};
   private static var _base64Lookup:Vector.<int> = null;

   public static function load(data:Object) : Vector.<Number>
   {
      if(data == null || !data.EMBEDDED) throw new Error("no exported policy weights are embedded");
      var checkpoint:String = String(data.CHECKPOINT_SHA256);
      if(_weightsByCheckpoint[checkpoint] != null) return _weightsByCheckpoint[checkpoint];
      loadStep(data,int(data.BASE64.length));
      return _weightsByCheckpoint[checkpoint];
   }

   public static function isLoaded(data:Object) : Boolean
   {
      return data != null && _weightsByCheckpoint[String(data.CHECKPOINT_SHA256)] != null;
   }

   public static function loadStep(data:Object, chunks:int) : Boolean
   {
      if(data == null || !data.EMBEDDED) throw new Error("no exported policy weights are embedded");
      var checkpoint:String = String(data.CHECKPOINT_SHA256);
      if(_weightsByCheckpoint[checkpoint] != null)
      {
         delete _stepsByCheckpoint[checkpoint];
         return true;
      }
      try
      {
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
            if(bytes.length % 4 != 0 || cursor + bytes.length / 4 > data.WEIGHT_COUNT)
               throw new Error("embedded policy chunk is not a complete float32 payload");
            bytes.endian = flash.utils.Endian.LITTLE_ENDIAN;
            bytes.position = 0;
            while(bytes.bytesAvailable > 0) result[cursor++] = bytes.readFloat();
         }
         step.cursor = cursor;
         step.chunk = stop;
         if(stop < int(data.BASE64.length)) return false;
         if(cursor != data.WEIGHT_COUNT)
            throw new Error("embedded policy weight count does not match its header");
         delete _stepsByCheckpoint[checkpoint];
         _weightsByCheckpoint[checkpoint] = result;
         return true;
      }
      catch(error:Error)
      {
         delete _stepsByCheckpoint[checkpoint];
         throw error;
      }
   }

   private static function base64Lookup() : Vector.<int>
   {
      if(_base64Lookup != null) return _base64Lookup;
      var result:Vector.<int> = new Vector.<int>(128,true);
      var index:int;
      for(index = 0; index < 128; index++) result[index] = -1;
      var alphabet:String = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
      for(index = 0; index < 64; index++) result[alphabet.charCodeAt(index)] = index;
      _base64Lookup = result;
      return result;
   }

   private static function decodeBase64(value:String) : flash.utils.ByteArray
   {
      if(value.length % 4 != 0) throw new Error("invalid embedded base64 length");
      var lookup:Vector.<int> = base64Lookup();
      var bytes:flash.utils.ByteArray = new flash.utils.ByteArray();
      bytes.endian = flash.utils.Endian.BIG_ENDIAN;
      var index:int = 0;
      // Preserve the final quartet for strict padding validation. Each full block
      // writes the same twelve raw bytes through three native calls, not twelve.
      var bulkEnd:int = value.length - 4;
      var a:int; var b:int; var c:int; var d:int;
      var e:int; var f:int; var g:int; var h:int;
      var i:int; var j:int; var k:int; var l:int;
      var m:int; var n:int; var o:int; var p:int;
      var q0:int; var q1:int; var q2:int; var q3:int;
      while(index + 16 <= bulkEnd)
      {
         a = lookup[value.charCodeAt(index)]; b = lookup[value.charCodeAt(index + 1)];
         c = lookup[value.charCodeAt(index + 2)]; d = lookup[value.charCodeAt(index + 3)];
         e = lookup[value.charCodeAt(index + 4)]; f = lookup[value.charCodeAt(index + 5)];
         g = lookup[value.charCodeAt(index + 6)]; h = lookup[value.charCodeAt(index + 7)];
         i = lookup[value.charCodeAt(index + 8)]; j = lookup[value.charCodeAt(index + 9)];
         k = lookup[value.charCodeAt(index + 10)]; l = lookup[value.charCodeAt(index + 11)];
         m = lookup[value.charCodeAt(index + 12)]; n = lookup[value.charCodeAt(index + 13)];
         o = lookup[value.charCodeAt(index + 14)]; p = lookup[value.charCodeAt(index + 15)];
         if((a | b | c | d | e | f | g | h | i | j | k | l | m | n | o | p) < 0)
            throw new Error("invalid embedded base64 weight data");
         q0 = (a << 18) | (b << 12) | (c << 6) | d;
         q1 = (e << 18) | (f << 12) | (g << 6) | h;
         q2 = (i << 18) | (j << 12) | (k << 6) | l;
         q3 = (m << 18) | (n << 12) | (o << 6) | p;
         bytes.writeUnsignedInt(uint((q0 << 8) | (q1 >>> 16)));
         bytes.writeUnsignedInt(uint((q1 << 16) | (q2 >>> 8)));
         bytes.writeUnsignedInt(uint((q2 << 24) | q3));
         index += 16;
      }
      for(; index < value.length; index += 4)
      {
         a = lookup[value.charCodeAt(index)];
         b = lookup[value.charCodeAt(index + 1)];
         if(a < 0 || b < 0) throw new Error("invalid embedded base64 weight data");
         if(value.charCodeAt(index + 2) == 61)
         {
            if(index + 4 != value.length || value.charCodeAt(index + 3) != 61 || (b & 15) != 0)
               throw new Error("invalid embedded base64 padding");
            bytes.writeByte((a << 2) | (b >> 4));
            continue;
         }
         c = lookup[value.charCodeAt(index + 2)];
         if(c < 0) throw new Error("invalid embedded base64 weight data");
         if(value.charCodeAt(index + 3) == 61)
         {
            if(index + 4 != value.length || (c & 3) != 0)
               throw new Error("invalid embedded base64 padding");
            bytes.writeByte((a << 2) | (b >> 4));
            bytes.writeByte(((b & 15) << 4) | (c >> 2));
            continue;
         }
         d = lookup[value.charCodeAt(index + 3)];
         if(d < 0) throw new Error("invalid embedded base64 weight data");
         bytes.writeByte((a << 2) | (b >> 4));
         bytes.writeByte(((b & 15) << 4) | (c >> 2));
         bytes.writeByte(((c & 3) << 6) | d);
      }
      return bytes;
   }
}
