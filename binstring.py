#bit

#test data
#data = 2
#numberofbits = 4

def bintofield(binin,noofbits):
    #add leading zeros when input is much smaller that number of bits required
    #for field
    zeros = ''
    c = 0
    if len(binin) < noofbits:
        while (c < noofbits - len(binin)):
            zeros = zeros + '0'
            c = c+1
        binin = zeros + binin
    else:    
    #strip unwanted leading 0s
        binin = binin[len(binin)-noofbits:len(binin)]
        
    #print (binin)    

    return binin


#integer to binary, striping off leading '0b' produced by bin() function
def datatobin(datain):
    output = bin(datain)[2:]

    #print (output)

    return output


#test main program
# print bintofield(datatobin(data),numberofbits)

#binary file number prefix zeros
def prefixzeros(dataint):
    if dataint < 10:
        prefixstr = '00'
    elif dataint < 100:
        prefixstr = '0'
    else:
        prefixstr = ''

    return prefixstr

#convert ASCII string to binary string
#input an ASCII string
#returns an ASCII string of 1s and 0s that represents the ASCII input
def strASCIItoBin(asciistr):
    BinaryString = ''
    c=0

    while c < len(asciistr):
        #print(c)
        BinaryString = BinaryString + bintofield(datatobin(ord(asciistr[c:c+1])),8)
        c=c+1

    return BinaryString

#generate an Iridium binary checksum
#input an ASCII representation (1s and 0s) of binary string
#returns ASCII string with checksum
def iridiumBinChksum(BinaryString):
    a = 0
    b = 8
    c = 0
    CharString = []
    checksum = 0

    while c < len(BinaryString):
        BinByte = BinaryString[a:b]
        #print (BinByte)
        CharByte = int(BinByte,2)
        #print (CharByte#, chr(CharByte))
        checksum = checksum + CharByte
        CharString.append(CharByte)
        a = a+8
        b = b+8
        c = c+8

    #append checksum to message
    #print (checksum)
    checksumdata = bintofield(datatobin(checksum),20)
    print (checksumdata)
    CharByte = int(checksumdata[-16:-8],2)
    #print (CharByte)
    CharString.append(CharByte)
    CharByte = int(checksumdata[-8:],2)
    #print (CharByte)
    CharString.append(CharByte)

    return CharString
